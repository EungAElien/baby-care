"""Real development-audio memorization and MPS resume gates for balanced v2."""

from __future__ import annotations

import gc
import time

import torch
from torch.nn import functional as F

from .balanced_data import DevelopmentStore, development_plan, small_balanced_subset, variant_config
from .checkpoint import (
    atomic_torch,
    frozen_digest,
    restore_training,
    resume_payload,
    state_digest,
)
from .data import load_feature
from .engine import make_optimizer, metrics_from_predictions, train_update
from .training import create_model
from .util import atomic_json, sha256_file, utc_now


def release_mps():
    gc.collect()
    torch.mps.empty_cache()


def sanity_check(base, protocol, paths, identity, guard):
    spec = protocol["sanity"]
    config = variant_config(base, protocol, "balanced_head")
    train = DevelopmentStore(config, paths).fold(spec["fold"])["donate"]["train"]
    rows = small_balanced_subset(
        train, config["datasets"]["donate"]["labels"], spec["seed"], spec["examples_per_label"]
    )
    model = create_model(config, paths, "B", spec["seed"]).to("mps")
    model.requires_grad_(False)
    model.heads.requires_grad_(True)
    model.eval()
    encoder_before = state_digest(model.encoder.state_dict())
    inputs = [
        load_feature(
            paths, config, "donate", row, seed=0, phase="validation", epoch=0, occurrence=0
        )
        .unsqueeze(0)
        .to("mps")
        for row in rows
    ]
    with torch.no_grad():
        features = []
        for value in inputs:
            guard.check()
            features.append(model.features(value))
        features = torch.cat(features)
    targets = torch.tensor([int(r["label_index"]) for r in rows], device="mps")
    head = model.heads["donate"]
    optimizer = torch.optim.AdamW(head.parameters(), lr=spec["head_lr"], weight_decay=0.01)
    history, gradients = [], []
    started = time.monotonic()
    for step in range(1, spec["maximum_updates"] + 1):
        guard.check()
        optimizer.zero_grad(set_to_none=True)
        logits = head(features)
        loss = F.cross_entropy(logits, targets)
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError("Non-finite sanity loss.")
        loss.backward()
        if step == 1:
            gradients = head.weight.grad.norm(dim=1).detach().cpu().tolist()
            if not all(value > 0 for value in gradients):
                raise RuntimeError("A classifier output has no gradient on the balanced subset.")
        torch.nn.utils.clip_grad_norm_(head.parameters(), 1.0, error_if_nonfinite=True)
        optimizer.step()
        with torch.no_grad():
            logits = head(features)
            observed_loss = float(F.cross_entropy(logits, targets))
            accuracy = float((logits.argmax(1) == targets).float().mean())
        if step == 1 or step % 10 == 0:
            history.append({"update": step, "cross_entropy": observed_loss, "accuracy": accuracy})
        if accuracy >= spec["required_accuracy"] and observed_loss <= spec["maximum_cross_entropy"]:
            break
    with torch.no_grad():
        cached = head(features).softmax(1).cpu()
        direct = torch.cat([model(value, "donate").softmax(1).cpu() for value in inputs])
    delta = float((cached - direct).abs().max())
    if state_digest(model.encoder.state_dict()) != encoder_before or delta > 1e-6:
        raise RuntimeError("Sanity encoder changed or raw-input/cached-feature outputs differ.")
    predictions = [
        {
            "sample_id": row["sample_id"],
            "group_id": row["group_id"],
            "true_index": int(row["label_index"]),
            "predicted_index": int(probs.argmax()),
            "probabilities": probs.tolist(),
        }
        for row, probs in zip(rows, direct, strict=True)
    ]
    passed = (
        accuracy >= spec["required_accuracy"] and observed_loss <= spec["maximum_cross_entropy"]
    )
    output = paths.output_root / "sanity"
    atomic_torch(output / "head.pt", {"identity": identity, "head": head.state_dict()}, 8)
    report = {
        "status": "passed" if passed else "failed",
        "identity": identity,
        "purpose": "TRAINING_MEMORIZATION_NOT_GENERALIZATION",
        "completed_utc": utc_now(),
        "sample_count": len(rows),
        "unique_groups": len({r["group_id"] for r in rows}),
        "updates": step,
        "cross_entropy": observed_loss,
        "accuracy": accuracy,
        "encoder_unchanged": True,
        "raw_vs_cached_max_probability_delta": delta,
        "first_update_gradient_norm_per_output": gradients,
        "metrics": metrics_from_predictions(predictions, model.labels["donate"]),
        "predictions": predictions,
        "history": history,
        "training_seconds": time.monotonic() - started,
        "artifact_sha256": {"head.pt": sha256_file(output / "head.pt")},
        "test_used": False,
    }
    atomic_json(output / "report.json", report)
    del optimizer, head, model, features, inputs
    release_mps()
    if not passed:
        raise RuntimeError("The fixed balanced-subset memorization gate failed; no matrix started.")
    return report


def resume_check(base, protocol, paths, identity, guard):
    config = variant_config(base, protocol, "balanced_finetune")
    manifests = DevelopmentStore(config, paths).fold(0)
    plan = development_plan(manifests, config, 42, "finetune", 3)["donate"]
    batch = config["training"]["finetune"]["effective_batch_per_dataset"]
    selected = [{"donate": plan[i * batch : (i + 1) * batch]} for i in range(2)]
    weights = {"donate": torch.ones(5)}

    def update(model, optimizer, index):
        started = time.monotonic()
        result = train_update(
            model,
            optimizer,
            paths,
            config,
            selected[index],
            weights,
            seed=42,
            phase="finetune",
            epoch=3,
            device="mps",
            interrupt=guard.check,
        )
        return {"seconds": time.monotonic() - started, **result}

    model = create_model(config, paths, "B", 42).to("mps")
    frozen = frozen_digest(model, config)
    before_tuned = state_digest(
        {k: v for k, v in model.encoder.state_dict().items() if k.startswith("blocks.11.")}
    )
    optimizer = make_optimizer(model, config, "finetune")
    first = update(model, optimizer, 0)
    checkpoint = paths.output_root / "pilot" / "resume.pt"
    atomic_torch(
        checkpoint,
        resume_payload(model, config, optimizer, {"step": 1}, identity, "finetune", "mps", frozen),
        8,
    )
    second = update(model, optimizer, 1)
    expected = state_digest(model.state_dict())
    if before_tuned == state_digest(
        {k: v for k, v in model.encoder.state_dict().items() if k.startswith("blocks.11.")}
    ):
        raise RuntimeError("The last encoder block did not update in the MPS pilot.")
    if frozen != frozen_digest(model, config):
        raise RuntimeError("The pilot modified frozen encoder tensors.")
    del optimizer, model
    release_mps()
    model = create_model(config, paths, "B", 42).to("mps")
    payload = torch.load(checkpoint, weights_only=True, map_location="cpu")
    optimizer, state, phase = restore_training(model, config, payload, identity, frozen, "mps")
    if state != {"step": 1} or phase != "finetune":
        raise RuntimeError("MPS resume cursor changed.")
    del payload
    update(model, optimizer, 1)
    observed = state_digest(model.state_dict())
    if expected != observed:
        raise RuntimeError("Continuous and resumed MPS training weights differ.")
    report = {
        "status": "passed",
        "identity": identity,
        "completed_utc": utc_now(),
        "continuous_and_resumed_sha256": observed,
        "exact_weight_match": True,
        "encoder_last_block_updated": True,
        "frozen_tensors_unchanged": True,
        "timings": [first, second],
        "test_used": False,
        "artifact_sha256": {"resume.pt": sha256_file(checkpoint)},
    }
    atomic_json(checkpoint.parent / "report.json", report)
    del optimizer, model
    release_mps()
    return report
