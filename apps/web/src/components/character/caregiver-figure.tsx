import { BabyFigure } from "./baby-figure";
import { type ActionCode, palette as p } from "./pose-catalog";

function CaregiverBody() {
  return <g data-part="caregiver">
    <path data-part="torso" d="M102 99 C52 108 33 158 40 220 L191 242 Q227 184 194 137 Q180 113 151 106Z" fill={p.cobalt} />
    <path d="M52 217 C11 232 21 271 64 277 L224 277 C272 277 284 243 251 230 Q218 219 182 239 Q101 213 52 217Z" fill={p.ink} />
    <g transform="rotate(12 125 85)">
      <path d="M87 73 L91 106 Q116 130 141 107 L150 80Z" fill={p.skin} />
      <path data-part="head" d="M86 48 Q102 21 142 37 Q171 49 160 82 Q155 112 132 112 Q105 111 90 83Z" fill={p.skin} />
      <path data-part="hair-back" d="M84 88 C62 81 65 58 78 49 C67 28 94 12 113 20 C139 -2 170 21 170 35 C193 45 184 70 162 76 L154 49 Q128 71 119 51 Q108 75 98 65 Q89 59 87 71Z" fill={p.ink} />
      <ellipse cx="90" cy="77" rx="10" ry="12" fill={p.skin} />
      <g fill={p.ink}><ellipse cx="126" cy="76" rx="3" ry="4.5" /><ellipse cx="149" cy="78" rx="3" ry="4.5" /></g>
      <path d="M135 81 L136 93 L131 94 M125 99 Q133 105 140 101" fill="none" stroke={p.coral} strokeWidth="3" strokeLinecap="round" />
      <path d="M119 65 Q124 62 131 66 M145 67 L152 70" fill="none" stroke={p.ink} strokeWidth="3" strokeLinecap="round" />
    </g>
  </g>;
}

export function CaregiverFigure({ action }: { action: ActionCode }) {
  const bedside = action === "diaper" || action === "sleeping" || action === "environment";
  const upright = action === "patting" || action === "burped";
  return <g data-action={action}>
    <CaregiverBody />
    {bedside ? <>
      <rect x="118" y="171" width="168" height="106" rx="15" fill={p.sage} />
      <rect x="128" y="181" width="148" height="80" rx="8" fill={p.ivory} />
      <g transform="translate(141 157) scale(.4) rotate(90 150 150)"><BabyFigure mood={action === "sleeping" ? "asleep" : "neutral"} lying mat={false} /></g>
      {action === "diaper" && <>
        <path d="M168 207 L181 208 L180 228 L167 227Z" fill={p.ivory} />
        <g data-motion="care-hand" fill="none" stroke={p.skin} strokeWidth="17" strokeLinecap="round"><path d="M73 165 Q107 202 177 209" /><path d="M186 153 Q220 177 178 229" /></g>
        <rect x="253" y="256" width="29" height="15" rx="5" fill={p.ivory} />
      </>}
      {action === "sleeping" && <g data-motion="care-hand" fill="none" stroke={p.skin} strokeWidth="17" strokeLinecap="round"><path d="M70 169 Q110 210 176 223" /><path d="M182 154 Q214 165 241 208" /></g>}
      {action === "environment" && <>
        <path d="M263 89 L263 165 M249 165 L278 165" stroke={p.ink} strokeWidth="6" strokeLinecap="round" />
        <path d="M247 81 L279 81 L290 122 L236 122Z" fill={p.sage} />
        <g data-motion="care-hand"><path d="M184 150 Q217 168 264 146" fill="none" stroke={p.skin} strokeWidth="17" strokeLinecap="round" /></g>
        <path d="M64 175 Q81 198 113 194" fill="none" stroke={p.skin} strokeWidth="20" strokeLinecap="round" />
      </>}
    </> : <g data-motion={action === "holding" ? "contact" : undefined}>
      {!upright && <path data-part="arm-back" d="M181 154 C240 133 257 178 237 212 Q226 240 178 244 L154 222 Q220 216 219 180 L203 173Z" fill={p.skin} />}
      <g transform={upright ? "translate(115 74) scale(.52)" : "translate(128 83) rotate(24 70 75) scale(.52)"}>
        <BabyFigure mood="neutral" />
      </g>
      {!upright && <path data-part="support-hand" d="M220 167 C224 139 211 133 207 147 L203 166 Q203 180 218 183Z" fill={p.skin} />}
      {action === "feeding" ? <path data-part="arm-front" data-motion="care-hand" d="M63 178 Q80 137 169 171" fill="none" stroke={p.skin} strokeWidth="23" strokeLinecap="round" />
        : action === "other" ? <path data-part="arm-front" data-motion="care-hand" d="M62 177 Q84 229 185 199" fill="none" stroke={p.skin} strokeWidth="25" strokeLinecap="round" />
          : <path data-part="arm-front" d="M55 177 C44 222 94 245 178 237 Q210 235 215 215 Q216 202 200 204 L171 214 Q108 223 87 180Z" fill={p.skin} />}
      {upright && <>
        <g data-motion="care-hand"><path data-part="arm-back" d="M184 149 Q240 132 244 181 Q245 200 222 197 L207 178 Q202 163 214 158 Q222 156 226 171 Q228 156 208 160Z" fill={p.skin} /></g>
        {action === "patting" && <path d="M249 164 Q259 172 257 182 M260 158 Q272 171 268 184" stroke={p.ink} strokeWidth="2.5" fill="none" strokeLinecap="round" />}
        {action === "burped" && <path d="M235 138 L244 138 M239 132 Q249 120 255 132 Q268 135 257 143 Q249 151 243 143" stroke={p.ink} strokeWidth="2.5" fill="none" strokeLinecap="round" />}
      </>}
    </g>}
  </g>;
}
