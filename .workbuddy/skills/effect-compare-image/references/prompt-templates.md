# 提示词模板（可直接复制）

所有模板都遵守同一条结构：**先 KEEP EXACTLY UNCHANGED 列死 → 再 ONLY 一个改动方向 → 最后统一画质段**。

参数固定：`size=1024x1536`、`quality=high`、输出到新的 `realN/` 目录。
`input_fidelity`：只换背景 `high`；需要实质改变体型/肤色 `medium`；`low` 禁用。

---

## 一、换背景（唯一改动是背景）

实测通过。关键三句不能删：`NOT a studio backdrop`、`slightly uneven and real`、`both halves must read as the same wall in the same room`。

```
Edit this photo. KEEP EXACTLY UNCHANGED: both women's faces, facial features,
expressions, hairstyles, hair strands on the shoulders, body posture, neck and
collarbone shape, the white cotton tank top with its exact folds, the framing and
crop, the vertical split position between the two halves, and the overall
warm-vs-cool skin tone difference between left and right.

ONLY replace the background.

New background: one single plain clean wall in very light warm grey-beige
(approx #EDE9E4), completely empty — no wooden wardrobe, no bed, no headboard,
no bedside table, no lamp, no bottles, no clothes, no clutter, no door frame,
no window. Just a smooth soft wall with a gentle natural light falloff toward
the corners, a faint soft shadow on the right side of each frame, and a barely
visible subtle wall texture. It must NOT look like a studio backdrop, NOT pure
white, NOT a gradient template — it looks like an ordinary bedroom wall at home,
slightly uneven and real. Both halves must read as the same wall in the same
room, just different light.

Authentic casual smartphone front-camera selfie quality: mild sensor noise,
slight softness, natural real skin with visible pores and fine texture, small
imperfections, no beauty filter, no skin smoothing, no retouching, no studio
lighting, no ring light, no HDR glow, no vignette effect added.
```

---

## 二、减重（明显档，全身均值 ≈ −17%）

实测通过（v9）。要点：**直接给百分比，且分部位**；否定式只挡"过度瘦化特征"，绝不写"do not make legs skinny"。

```
Edit this side-by-side mirror selfie.

LEFT panel: keep it exactly as it is. Do not change the left figure in any way.

RIGHT panel: make her noticeably slimmer than she is right now — the current
difference between the two panels is still too small and reads as the same body.
She should look like she lost about 25 pounds / 12 kg over four months, through
eating less and walking daily.

The reduction must be even and whole-body, about 15 percent everywhere, slightly
more in the legs:
- shoulders and upper back about 10 percent narrower
- arms clearly thinner
- waist about 15 percent smaller, belly about 15 percent smaller — but still soft,
  a real woman's belly with a gentle fold, no flat washboard stomach, no ab lines,
  no six-pack, no visible ribs, no hip bones
- hips and buttocks about 15 percent smaller
- thighs about 20 percent thinner — a clear daylight gap between the upper thighs
  just below the shorts, and the outer thigh line curves inward instead of bulging
- calves and ankles about 18 percent slimmer
- legs keep normal length and normal proportion to the torso, no spindly or
  elongated legs
- face almost unchanged: same jawline, same cheeks, same nose, same eyes, only
  the tiniest bit less full. She must obviously be the same person as the left
  panel, just slimmer.

She must look like an ordinary woman who lost weight, not a fitness model or
athlete. No muscle definition, no toned look, no training aesthetic.

Both figures must be exactly the same height in the frame, feet on the same floor
line, same distance from the camera.

Keep everything else identical: the white wardrobe doors, the wood floor, the
skirting board, the ceiling light, the laundry basket, the phone covering her
face, the black sports bra and shorts, the poses, the framing and the vertical
split.

Authentic amateur phone selfie quality: mild noise, natural skin texture with
visible pores, warm indoor light, no beauty filter, no skin smoothing, no studio
lighting.
```

---

## 三、减重（温和档，全身均值 ≈ −11%）

实测通过（v8）。用于 2–3 个月、保守表述。把目标百分比整体压低即可，其余结构不变。

```
RIGHT panel: the SAME woman after only a modest, realistic weight loss.
- belly: still soft and slightly rounded, a small natural pooch,
  no ab lines, no six-pack, no visible ribs or hip bones
- waist: only slightly narrower than her hips, not a defined hourglass
- thighs: normal thickness, only a slight gap between the legs, normal leg length
- arms: normal soft arms, not thin sticks
- face: same face shape as the LEFT panel, cheeks still full, same jaw, same nose,
  same eyes — only marginally less puffy. No chin sharpening, no nose slimming.

Both figures must be exactly the same height in the frame and stand at the same
distance from the mirror: head top, shoulder, waist and knee at identical
vertical positions on both sides.
```

若上一版已经接近但差一点点，**不要在旧图上直接重生成求微调**——模型对"再瘦 2%"没有响应能力，反而会把脸和腰腹改坏。要么接受，要么换档位重做。

---

## 四、肤色变化（面部只能脸，身体才到颈肩）

规则来自品类与合规约束，不是审美偏好：

- **化妆品（上脸）**：只能脸变白，颈以下不许变白，否则等于宣称身体功效
- **保健食品（口服）**：可到颈部、肩、手臂，但脸也要同步（不能出现"脸白脖子黑"的断层）

```
RIGHT panel: the same woman after four weeks. Only her face tone changes —
even out the dullness and yellow cast, slightly brighter and more even, but keep
the same skin texture, same pores, same small imperfections. Do NOT lighten the
neck, shoulders or body. Do NOT smooth or airbrush the skin.

Both panels must keep: identical face shape, same eyes, same nose, same lips,
same hairline, same hair strands, same clothing with the same folds, same framing
and same vertical split, and the same warm indoor light.
```

身体版把 "Only her face tone changes" 换成 `face, neck, shoulders and arms tone`，并补一句 `the boundary must fade naturally at the collarbone — no visible line`。

---

## 五、水印修补（脚本模板）

生成图几乎必带右下角水印。**唯一干净的做法**：正上方同宽同高取纹理回补。

```python
from pathlib import Path
import numpy as np
from PIL import Image

SRC = Path("产出/ai人物/realN/xxx.png")
OUT = SRC.parent / "clean.png"
rgb = np.asarray(Image.open(SRC).convert("RGB")).astype(np.float32)
h, w, _ = rgb.shape

x0, y0 = int(w * 0.815), int(h * 0.925)      # 覆盖整块水印，宁大勿小
pw, ph = w - x0, h - y0

# 正上方、同宽、同高（先确认上方是干净背景，不是踢脚线/脚）
src = rgb[y0 - ph - 6:y0 - 6, x0:w].copy()
src += rgb[y0 - ph - 6:y0 - 6, x0:w].mean(axis=(0, 1)) - src.mean(axis=(0, 1))

fx, fy = 36, 24
xx = np.arange(pw)[None, :]
yy = np.arange(ph)[:, None]
m = (np.clip(xx / fx, 0, 1) ** 1.5 * np.clip(yy / fy, 0, 1) ** 1.5)[..., None]

out = rgb.copy()
out[y0:h, x0:w] = rgb[y0:h, x0:w] * (1 - m) + src * m
rng = np.random.default_rng(59)
out[y0:h, x0:w] += rng.normal(0, 1.5, (ph, pw, 1)) * m

Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(OUT)
```

修完必须裁出右下角放大 2.6 倍看一眼，确认四件事：无水印残字、无矩形边界、纹理连贯、没有复制出第二只脚/第二个物体。

---

## 六、共性写法

| 位置 | 内容 |
|---|---|
| 第一段 | `KEEP EXACTLY UNCHANGED:` 后接所有不许动的项，用逗号连成一长句，越具体越好 |
| 第二段 | 只用一次 `ONLY …`，只给一个改动方向 |
| 第三段 | 目标状态，**分部位逐条**，给百分比或明确的形态描述 |
| 第四段 | 独立强约束：`both figures must be exactly the same height in the frame` |
| 末段 | 画质段，固定不变：真实皮肤纹理、毛孔、轻微噪点、无美颜、无影棚光 |

画质段每次都要写全。漏掉"no skin smoothing / no beauty filter"，模型默认会开美颜，出来的就是塑料感。
