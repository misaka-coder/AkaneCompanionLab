# magnetic_chaos_pptmaster_try - Design Spec

> Human-readable design narrative. The machine-readable execution contract is `spec_lock.md`; if the two diverge, `spec_lock.md` wins.

## I. Project Information

| Item | Value |
| ---- | ----- |
| **Project Name** | magnetic_chaos_pptmaster_try |
| **Canvas Format** | PPT 16:9 |
| **Page Count** | 7 |
| **Design Style** | General Versatile + clean science education |
| **Target Audience** | 老师与同学 |
| **Use Case** | 5 分钟实验讲解视频，PPT 与横屏演示视频穿插 |
| **Created Date** | 2026-05-26 |

---

## II. Canvas Specification

| Property | Value |
| -------- | ----- |
| **Format** | ppt169 |
| **Dimensions** | 1280 × 720 |
| **viewBox** | `0 0 1280 720` |
| **Margins** | left/right 64px, top 48px, bottom 44px |
| **Content Area** | 1152 × 628 |

---

## III. Visual Theme

### Theme Style

- **Style**: clean science education
- **Theme**: Light theme
- **Tone**: 清爽、理性、课堂友好，避免实验照片堆叠

### Color Scheme

| Role | HEX | Purpose |
| ---- | --- | ------- |
| **Background** | `#F8FAFC` | 页面主背景 |
| **Secondary bg** | `#FFFFFF` | 内容板块和视频占位底色 |
| **Soft bg** | `#EEF6FF` | 科普示意区域 |
| **Primary** | `#2563EB` | 标题、主线条、重点符号 |
| **Secondary** | `#0D9488` | 科学感辅助色、正向提示 |
| **Accent** | `#EA580C` | 视频占位、关键提醒 |
| **Purple** | `#7C3AED` | 混沌/敏感性强调 |
| **Body text** | `#1A202C` | 正文 |
| **Secondary text** | `#526070` | 说明文字 |
| **Tertiary text** | `#64748B` | 页脚、标签 |
| **Border/divider** | `#CBD5E1` | 边框、辅助线 |
| **Line soft** | `#D9E2EC` | 网格线、示意线 |
| **Yellow** | `#FACC15` | 小球 |

---

## IV. Typography

| Role | Font | Size Guidance |
| ---- | ---- | ------------- |
| **Title** | Microsoft YaHei | 44px to 56px |
| **Subtitle** | Microsoft YaHei | 24px to 30px |
| **Body** | Microsoft YaHei | 22px to 26px |
| **Annotation** | Microsoft YaHei | 16px to 19px |
| **Hero** | Microsoft YaHei | 76px to 90px |

Formula policy: text-only. The deck does not need formula rendering.

---

## V. Layout System

- Use 16:9 full-page slides.
- Video placeholder pages use a large 16:9 frame on the right or center, marked clearly as "横屏演示视频".
- Non-video explanation pages use simple diagrams, callouts, and one to three information blocks.
- Avoid making every page a card grid; mix cover, video frame, force diagram, and conclusion page.

---

## VI. Icon System

No external icon library is required. Use simple inline geometric symbols: circles for magnets and ball, connector lines for forces, small tags for video placeholders.

---

## VII. Visualization Plan

| Page | Visualization |
| ---- | ------------- |
| 03 | Device schematic: pendulum string, ball, three magnets |
| 05 | Two nearby starting points diverging into different paths |
| 06 | Observation checklist and video ending frame |

No data charts are used.

---

## VIII. Image Resource List

| ID | Acquire Via | Status | Usage |
| -- | ----------- | ------ | ----- |
| video_slot_1 | placeholder | ready | 释放与初始摆动视频位置 |
| video_slot_2 | placeholder | ready | 复杂运动视频位置 |
| video_slot_3 | placeholder | ready | 变慢并停下视频位置 |

Experiment photos are not used as main page imagery in this version.

---

## IX. Content Outline

### P01 封面

Title: 实验 27：磁混沌摆  
Message: 一个小球为什么会走出复杂轨迹，最后停在某一个磁铁上？

### P02 现象观察

Explain the observed motion: bending, turning, slowing down, stopping above one magnet.

### P03 演示视频一

Large horizontal video placeholder for "release and initial motion"; left side lists what to watch.

### P04 装置组成

Show simplified device schematic and explain pendulum, magnets, release position.

### P05 原理解释

Explain gravity, magnetic force, friction/resistance, and nonlinear sensitivity.

### P06 混沌含义

Explain deterministic but hard to predict; two nearly identical starts can lead to different paths.

### P07 结论与视频收束

Summarize the lesson and reserve a final video placeholder for the ball stopping.

---

## X. Speaker Notes Plan

Each page gets concise notes matching a 5-minute narration. Video pages include cue lines telling the presenter what the audience should observe.

---

## XI. Technical Constraints

- SVG canvas: 1280 × 720.
- Use inline SVG attributes only.
- Use PPT-safe font `Microsoft YaHei`.
- Avoid external raster images in this version.
- Use no masks, no CSS classes, no foreignObject, no script.
- All video placeholders are static PPT shapes; actual video insertion happens manually in PowerPoint or editing software.
