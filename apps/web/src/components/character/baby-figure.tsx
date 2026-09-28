import { palette as p } from "./pose-catalog";

export type BabyMood = "neutral" | "crying" | "fussing" | "calm" | "cheerful" | "awake" | "sleepy" | "asleep" | "hungry" | "tired" | "burping" | "belly_pain" | "discomfort";

const arms: Partial<Record<BabyMood, readonly [string, string]>> = {
  crying: ["M105 175 Q78 193 73 147", "M195 175 Q221 192 228 148"],
  fussing: ["M105 175 Q88 202 135 191", "M195 175 Q201 207 153 191"],
  calm: ["M105 175 Q85 206 134 209", "M195 175 Q218 206 167 209"],
  cheerful: ["M105 175 Q94 197 123 196", "M195 175 Q220 166 227 129"],
  sleepy: ["M105 175 Q93 211 111 237", "M195 175 Q216 193 191 153"],
  hungry: ["M105 175 Q82 181 142 151", "M195 175 Q210 216 159 218"],
  tired: ["M105 175 Q80 177 122 129", "M195 175 Q206 222 192 240"],
  burping: ["M105 175 Q83 198 146 179", "M195 175 Q208 215 219 224"],
  belly_pain: ["M105 178 Q101 209 140 221", "M195 178 Q198 209 161 221"],
  discomfort: ["M105 175 Q78 201 67 171", "M195 175 Q222 182 226 153"],
  asleep: ["M105 175 Q87 194 74 187", "M195 175 Q213 194 226 187"],
};

function BabyFace({ mood }: { mood: BabyMood }) {
  const squeezed = mood === "crying" || mood === "belly_pain" || mood === "discomfort";
  const sleepy = mood === "sleepy" || mood === "tired";
  const closed = mood === "asleep";
  const tense = ["crying", "fussing", "hungry", "burping", "belly_pain", "discomfort"].includes(mood);
  return <>
    <g data-part="brows" fill="none" stroke={p.ink} strokeWidth="3.5" strokeLinecap="round">
      <path d={tense ? "M112 103 Q122 106 129 97 M172 97 Q179 107 189 104" : "M113 101 Q121 96 129 100 M172 100 Q180 96 188 101"} />
    </g>
    <g data-part="eyes" fill={p.ink}>
      {squeezed ? <path d="M112 117 L126 124 L113 128 M188 117 L174 124 L188 128" fill="none" stroke={p.ink} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
        : closed ? <path d="M113 122 Q121 130 129 122 M172 122 Q180 130 188 122" fill="none" stroke={p.ink} strokeWidth="3.5" strokeLinecap="round" />
          : <g className={sleepy ? undefined : "character-eyes"}>
            <ellipse cx="121" cy={sleepy ? 127 : 122} rx="6" ry={sleepy ? 4 : 8} />
            <ellipse cx="180" cy={sleepy ? 127 : 122} rx="6" ry={sleepy ? 4 : 8} />
            {sleepy && <path d="M111 122 L130 123 M171 123 L190 121" stroke={p.ink} strokeWidth="3" />}
          </g>}
    </g>
    <ellipse cx="150" cy="134" rx="4" ry="2.8" fill={p.coral} />
    <g data-part="mouth">
      {mood === "crying" ? <path d="M132 157 C130 135 170 135 169 158 Q167 166 158 159 Q148 153 140 162 Q132 166 132 157" fill={p.coral} />
        : sleepy ? <ellipse cx="151" cy="151" rx="9" ry="13" fill={p.coral} />
          : mood === "cheerful" ? <path d="M133 143 Q151 150 172 141 Q171 167 151 168 Q134 165 133 143" fill={p.coral} />
            : mood === "hungry" || mood === "burping" ? <ellipse cx="150" cy="151" rx={mood === "hungry" ? 6 : 3.5} ry={mood === "hungry" ? 8 : 4.5} fill={p.ink} />
              : <path d={mood === "calm" ? "M143 148 Q151 156 160 148" : tense ? "M142 153 Q150 143 159 152" : "M146 150 L155 151"} fill="none" stroke={p.ink} strokeWidth="3" strokeLinecap="round" />}
    </g>
    {mood === "crying" && <g data-part="tears" fill={p.sage}><path d="M101 133 Q86 151 99 152 Q109 150 101 133" /><path d="M198 134 Q185 150 198 152 Q208 150 198 134" /></g>}
  </>;
}

/** Independently editable paths. Layout transforms remain outside motion groups. */
export function BabyFigure({ mood = "neutral", lying = false, mat = true }: { mood?: BabyMood; lying?: boolean; mat?: boolean }) {
  const arm = (lying ? arms.asleep : arms[mood]) ?? ["M105 175 Q101 213 116 238", "M195 175 Q199 213 183 238"];
  const curled = mood === "belly_pain";
  const tilt = mood === "hungry" ? -7 : mood === "tired" || mood === "sleepy" ? -6 : mood === "discomfort" ? 8 : mood === "burping" ? -3 : 0;
  return <g data-part="baby">
    {lying && mat && <rect x="57" y="35" width="186" height="253" rx="24" fill={p.sage} />}
    <g transform={curled ? "translate(8 25) scale(.95 .92)" : undefined}>
      <g className="character-body" data-part="body">
        <path d="M111 157 Q149 173 190 157 C202 181 212 225 201 245 Q176 272 111 254 C86 244 96 187 111 157" fill={p.ochre} />
        <g data-part="left-leg" className="character-leg-left" fill={p.skin}>
          <path d={lying ? "M115 229 Q93 226 99 252 L110 272 Q98 282 115 283 Q141 281 129 255 L133 241Z" : curled ? "M115 224 C89 201 76 224 93 249 Q109 272 145 263 Q157 254 141 244 Z" : mood === "discomfort" ? "M111 231 Q98 224 79 240 L51 239 Q37 248 56 260 Q84 274 124 254Z" : "M116 231 C98 214 77 227 86 247 C61 230 57 251 79 263 Q117 282 131 255Z"} />
        </g>
        <g data-part="right-leg" className="character-leg-right" fill={p.skin}>
          <path d={lying ? "M185 229 Q207 226 201 252 L190 272 Q202 282 185 283 Q159 281 171 255 L167 241Z" : curled ? "M184 224 C210 204 223 226 205 251 Q189 272 158 263 Q148 252 161 244Z" : mood === "cheerful" ? "M188 230 Q199 209 218 219 Q240 201 243 219 Q241 244 209 249Z" : "M184 232 C201 214 224 227 215 247 C240 230 244 251 222 263 Q184 282 169 255Z"} />
        </g>
        <g transform={`rotate(${tilt} 150 155)`}>
          <g data-part="head" className="character-head">
            <g fill={p.skin}><ellipse cx="87" cy="127" rx="13" ry="16" /><ellipse cx="213" cy="127" rx="13" ry="16" />
              <path d="M91 121 C86 78 108 49 146 48 C190 44 214 74 211 122 C213 153 187 173 150 173 C117 173 92 155 91 121Z" /></g>
            <path data-part="curl" d="M148 52 C164 49 168 37 157 32 C179 27 185 49 170 59 C154 69 139 65 148 52Z" fill={p.ink} />
            <BabyFace mood={mood} />
          </g>
        </g>
        <g fill="none" stroke={p.skin} strokeWidth="23" strokeLinecap="round" strokeLinejoin="round">
          <g data-part="left-arm" className="character-arm-left"><path d={arm[0]} /></g>
          <g data-part="right-arm" className="character-arm-right"><path d={arm[1]} /></g>
        </g>
      </g>
    </g>
  </g>;
}
