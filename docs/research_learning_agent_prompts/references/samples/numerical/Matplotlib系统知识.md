---
tags:
  - python/matplotlib
  - visualization
  - signal-processing
created: 2026-05-12
---
出来的数据变成**看得见的图**——信号波形、样本分布、训练曲线、混淆矩阵。

对电子信息 / AI 方向，Matplotlib 是 NumPy 的天然搭档：

```text
正弦信号波形          训练损失曲线
带噪声信号            模型准确率曲线
传感器数据变化         混淆矩阵
特征分布              图片数据
```

---

## 一、导入 Matplotlib

标准写法：

```python
import matplotlib.pyplot as plt
```

通常和 NumPy 配合：

```python
import numpy as np
import matplotlib.pyplot as plt
```

以后看到 `plt.plot()`、`plt.scatter()`、`plt.show()`——`plt` 就是 `matplotlib.pyplot`。

---

## 二、最基本的画图流程

```python
import matplotlib.pyplot as plt

x = [1, 2, 3, 4]
y = [2, 4, 6, 8]

plt.plot(x, y)     # 画折线图
plt.show()         # 显示图像
```

```text
x：横坐标
y：纵坐标
plt.plot(x, y)：画线
plt.show()：显示窗口
```

> ⚠️ 脚本里忘记 `plt.show()`，图不会弹出来。

---

## 三、配合 NumPy 画函数图像

$$
y = x^2
$$

```python
import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(-5, 5, 100)    # -5 到 5 取 100 个点
y = x ** 2

plt.plot(x, y)
plt.show()
```

```python
x = np.linspace(-5, 5, 100)     # NumPy 生成 x 轴数据
y = x ** 2                       # 向量化计算，每个 x 算一次平方
```

---

## 四、标题、坐标轴、图例

一张完整的图需要这些元素：

```python
import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 10, 100)
y = x ** 2

plt.plot(x, y, label="y = x^2")    # label 用于图例

plt.title("Quadratic Function")    # 标题
plt.xlabel("x")                    # x 轴标签
plt.ylabel("y")                    # y 轴标签
plt.legend()                       # 显示图例
plt.grid(True)                     # 显示网格

plt.show()
```

| 函数 | 作用 |
|------|------|
| `plt.title()` | 标题 |
| `plt.xlabel()` | x 轴名称 |
| `plt.ylabel()` | y 轴名称 |
| `plt.legend()` | 显示图例（配合 label 使用） |
| `plt.grid(True)` | 显示网格 |

> ⚠️ 写了 `label="..."` 但忘了 `plt.legend()`，图例不会显示。

---

## 五、中文显示问题

默认不支持中文，会变方块。加两行配置：

```python
plt.rcParams["font.sans-serif"] = ["SimHei"]         # 设置中文字体
plt.rcParams["axes.unicode_minus"] = False           # 正常显示负号
```

完整例子：

```python
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

x = np.linspace(0, 10, 100)
y = x ** 2

plt.plot(x, y)
plt.title("二次函数")
plt.xlabel("横坐标 x")
plt.ylabel("纵坐标 y")
plt.show()
```

> 如果电脑没有 SimHei 字体可能还是方块，项目里用英文标题最省事。

---

## 六、画正弦信号 — 电子信息最经典的用法

正弦信号公式：

$$
x(t)=A\sin(2\pi ft)
$$

```python
import numpy as np
import matplotlib.pyplot as plt

A = 1.0          # 幅值
f = 50           # 频率 (Hz)
duration = 0.1   # 时长 (s)
sample_count = 1000

t = np.linspace(0, duration, sample_count)
signal = A * np.sin(2 * np.pi * f * t)

plt.plot(t, signal)
plt.title("Sine Signal (50 Hz)")
plt.xlabel("Time (s)")
plt.ylabel("Amplitude")
plt.grid(True)
plt.show()
```

```text
t 是时间轴（1000 个点）
signal 是每个时间点对应的信号值
```

### 离散信号 `stem()`

电子信息方向画采样点、离散序列，`stem` 比 `plot` 更合适：

```python
import numpy as np
import matplotlib.pyplot as plt

n = np.arange(0, 10)
x = np.sin(n * np.pi / 4)

plt.stem(n, x)
plt.title("Discrete Signal")
plt.xlabel("n")
plt.ylabel("x[n]")
plt.grid(True)
plt.show()
```

---

## 七、画多条曲线

同时画干净信号和带噪声信号：

```python
import numpy as np
import matplotlib.pyplot as plt

A = 1.0
f = 50
t = np.linspace(0, 0.1, 1000)

clean_signal = A * np.sin(2 * np.pi * f * t)
noise = 0.2 * np.random.randn(1000)
noisy_signal = clean_signal + noise

plt.plot(t, clean_signal, label="Clean Signal")
plt.plot(t, noisy_signal, label="Noisy Signal")

plt.title("Clean vs Noisy Signal")
plt.xlabel("Time (s)")
plt.ylabel("Amplitude")
plt.legend()
plt.grid(True)
plt.show()
```

关键是每根线有 `label`，再调 `plt.legend()` 显示。

---

## 八、线条样式

```python
plt.plot(x, y, linestyle="--", marker="o", linewidth=2, markersize=6, label="data")
```

| 参数 | 作用 | 常用值 |
|------|------|--------|
| `linestyle` | 线条样式 | `"-"` 实线、`"--"` 虚线、`":"` 点线、`"-."` 点划线 |
| `marker` | 数据点标记 | `"o"` 圆点、`"s"` 方块、`"^"` 三角、`"x"` 叉号 |
| `linewidth` | 线宽 | 数字，如 `2` |
| `markersize` | 点大小 | 数字，如 `6` |
| `label` | 图例名称 | 字符串 |
| `color` | 线条/点颜色 | `"red"`、`"#FF5733"`、`"C1"`（色轮第 2 种） |
| `alpha` | 透明度 | `0.0`（全透明） ~ `1.0`（不透明），重合数据常用 `0.5` |

透明度例子：

```python
# 散点图数据重叠时设 alpha 能看清密度
plt.scatter(x, y, alpha=0.5)
```

---

## 九、坐标轴范围

只看信号的局部：

```python
import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 1, 1000)
signal = np.sin(2 * np.pi * 50 * t)

plt.plot(t, signal)
plt.xlim(0, 0.1)       # 只显示前 0.1 秒
plt.ylim(-1.2, 1.2)    # y 轴范围
plt.grid(True)
plt.show()
```

两种看局部的方式：

```python
plt.xlim(0, 0.02)                      # 数据还在，只改显示范围
plt.plot(t[:200], noisy_signal[:200])   # 只取前 200 个点画
```

### 对数坐标

跨度很大的数据，用对数坐标更直观：

```python
import numpy as np
import matplotlib.pyplot as plt

x = np.arange(1, 10)
y = 10 ** x

plt.plot(x, y)
plt.yscale("log")          # y 轴对数
plt.title("Log Scale")
plt.xlabel("x")
plt.ylabel("y")
plt.grid(True)
plt.show()
```

常见于频谱幅值跨度大、损失下降很快、误差数量级变化。

---

## 十、散点图 scatter

观察数据点分布：

```python
import matplotlib.pyplot as plt

x = [1, 2, 3, 4, 5]
y = [2.1, 3.9, 6.2, 8.1, 9.8]

plt.scatter(x, y)
plt.title("Scatter Plot")
plt.xlabel("x")
plt.ylabel("y")
plt.grid(True)
plt.show()
```

AI 里最常见的用法——样本特征分布：

```python
import numpy as np
import matplotlib.pyplot as plt

X = np.array([
    [1.0, 2.0],
    [1.5, 1.8],
    [5.0, 8.0],
    [6.0, 8.5]
])

plt.scatter(X[:, 0], X[:, 1])      # 第 1 个特征作 x，第 2 个特征作 y
plt.xlabel("Feature 1")
plt.ylabel("Feature 2")
plt.title("Feature Distribution")
plt.grid(True)
plt.show()
```

---

## 十一、带类别的散点图

```python
import numpy as np
import matplotlib.pyplot as plt

X = np.array([
    [1.0, 2.0],
    [1.5, 1.8],
    [5.0, 8.0],
    [6.0, 8.5]
])
y = np.array([0, 0, 1, 1])    # 两类标签

plt.scatter(X[y == 0, 0], X[y == 0, 1], label="Class 0")
plt.scatter(X[y == 1, 0], X[y == 1, 1], label="Class 1")

plt.xlabel("Feature 1")
plt.ylabel("Feature 2")
plt.title("Two Classes")
plt.legend()
plt.grid(True)
plt.show()
```

这里用到了 NumPy 布尔索引：`X[y == 0, 0]` 取出标签为 0 的样本的第 1 个特征。

---

## 十二、柱状图 bar

比较不同对象：

```python
import matplotlib.pyplot as plt

sensor_names = ["S1", "S2", "S3", "S4"]
mean_voltages = [3.2, 3.0, 3.6, 3.1]

plt.bar(sensor_names, mean_voltages)
plt.title("Mean Voltage per Sensor")
plt.xlabel("Sensor")
plt.ylabel("Mean Voltage (V)")
plt.show()
```

AI 方向：比较模型准确率。

```python
models = ["KNN", "SVM", "CNN"]
accuracies = [0.82, 0.88, 0.93]

plt.bar(models, accuracies)
plt.title("Model Accuracy")
plt.xlabel("Model")
plt.ylabel("Accuracy")
plt.ylim(0, 1)
plt.show()
```

---

## 十三、直方图 hist

看数据分布：

```python
import numpy as np
import matplotlib.pyplot as plt

noise = np.random.randn(1000)

plt.hist(noise, bins=30)         # bins 表示分多少个区间
plt.title("Noise Distribution")
plt.xlabel("Value")
plt.ylabel("Count")
plt.show()
```

适合看：噪声分布、电压分布、模型误差分布、成绩分布。

---

## 十四、箱线图 boxplot

观察整体分布和离群点：

```python
import numpy as np
import matplotlib.pyplot as plt

data = [
    np.random.randn(100),
    np.random.randn(100) + 1,
    np.random.randn(100) + 2
]

plt.boxplot(data)
plt.title("Boxplot")
plt.xlabel("Group")
plt.ylabel("Value")
plt.show()
```

可以看：中位数、上下四分位数、离散程度、离群点。AI 实验里用来比较多组结果。

---

## 十五、imshow 显示矩阵或图片

```python
import numpy as np
import matplotlib.pyplot as plt

matrix = np.array([
    [1, 2, 3],
    [4, 5, 6],
    [7, 8, 9]
])

plt.imshow(matrix)
plt.colorbar()               # 颜色对应的数值条
plt.title("Matrix Image")
plt.show()
```

灰度图（MNIST 手写数字就是 28×28 的灰度图）：

```python
image = np.random.rand(28, 28)

plt.imshow(image, cmap="gray")
plt.colorbar()
plt.title("Random 28×28 Image")
plt.show()
```

```text
灰度图：二维数组（高度 × 宽度）
彩色图：三维数组（高度 × 宽度 × 颜色通道）
```

---

## 十六、混淆矩阵可视化

AI 分类任务标配：

```python
import numpy as np
import matplotlib.pyplot as plt

confusion_matrix = np.array([
    [50, 2, 1],
    [3, 45, 5],
    [0, 4, 48]
])

plt.imshow(confusion_matrix)
plt.colorbar()
plt.title("Confusion Matrix")
plt.xlabel("Predicted Label")
plt.ylabel("True Label")
plt.show()
```

---

## 十七、子图 subplot

多张图放在一个窗口：

```python
import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 0.1, 1000)

plt.subplot(2, 1, 1)    # 2 行 1 列，第 1 张图
plt.plot(t, np.sin(2 * np.pi * 50 * t))
plt.title("50 Hz Signal")

plt.subplot(2, 1, 2)    # 2 行 1 列，第 2 张图
plt.plot(t, np.sin(2 * np.pi * 100 * t))
plt.title("100 Hz Signal")

plt.tight_layout()       # 防止标题重叠
plt.show()
```

```text
plt.subplot(行, 列, 第几张)
plt.tight_layout()：自动调整间距
```

### 网格子图（最常用写法）

上面是竖直堆叠。实际中更常用 **行列网格**：

```python
fig, axes = plt.subplots(2, 2, figsize=(10, 8))    # 2 行 2 列

axes[0, 0].plot(x, y1)
axes[0, 0].set_title("图 1")
axes[0, 1].plot(x, y2)
axes[0, 1].set_title("图 2")
axes[1, 0].plot(x, y3)
axes[1, 0].set_title("图 3")
axes[1, 1].plot(x, y4)
axes[1, 1].set_title("图 4")

plt.tight_layout()
plt.show()
```

这里 `axes[0, 0]` 表示第 0 行第 0 列那个子图。每个子图独立操作，比 `plt.subplot()` 更灵活。

**共享坐标轴 `sharex` / `sharey`**：

```python
# 上下对齐显示多通道信号，拖动一张另一张同步缩放
fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

axes[0].plot(t, signal1)
axes[0].set_title("Channel 1")
axes[1].plot(t, signal2)
axes[1].set_title("Channel 2")
axes[2].plot(t, signal3)
axes[2].set_title("Channel 3")
axes[2].set_xlabel("Time (s)")

plt.tight_layout()
plt.show()
```

`sharex=True` 让所有子图 x 轴联动——缩放一张图，其他图同步。多通道信号分析必备。

---

## 十八、Figure 和 Axes 概念

```text
Figure：整张画布（一张纸）
Axes：  画布里的一个坐标系（纸上的一块绘图区）
```

更多子图时，推荐的写法：

```python
import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 10, 100)

fig, ax = plt.subplots()     # 创建一张画布和一个坐标轴

ax.plot(x, np.sin(x))
ax.set_title("Sine")
ax.set_xlabel("x")
ax.set_ylabel("sin(x)")
ax.grid(True)

plt.show()
```

| pyplot 写法 | 面向对象写法 |
|------------|------------|
| `plt.plot()` | `ax.plot()` |
| `plt.title()` | `ax.set_title()` |
| `plt.xlabel()` | `ax.set_xlabel()` |
| `plt.ylabel()` | `ax.set_ylabel()` |
| `plt.xlim()` | `ax.set_xlim()` |
| `plt.grid()` | `ax.grid()` |

> pyplot 写法简单适合快速画图，面向对象写法适合复杂图和多子图。初学用 `plt.xxx` 就够了。

### 控制图片大小 `figsize`

默认图太小了，加 `figsize` 参数：

```python
# pyplot 写法
plt.figure(figsize=(10, 4))    # 宽 10 英寸，高 4 英寸
plt.plot(x, y)
plt.show()

# 面向对象写法（推荐）
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(x, y)
plt.show()
```

横向长的图适合看信号时序，方形图适合看散点分布。

---

## 十九、保存图片

```python
plt.plot(x, y)
plt.savefig("signal.png", dpi=300, bbox_inches="tight")   # 放在 show() 前面
plt.show()
```

| 参数 | 作用 |
|------|------|
| `dpi=300` | 清晰度，300 足够论文用 |
| `bbox_inches="tight"` | 自动裁剪多余空白 |

支持格式：`.png`、`.jpg`、`.pdf`、`.svg`。

---

## 二十、辅助线

```python
plt.axhline(3.3, linestyle="--", color="red", label="Limit 3.3V")    # 水平线
plt.axvline(0.05, linestyle=":", color="gray")                        # 竖直线
```

常用于标注阈值、分界线。

---

## 二十一、图上标注文字 `text()` / `annotate()`

标出关键数据点：

```python
import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 0.1, 1000)
signal = np.sin(2 * np.pi * 50 * t)

plt.plot(t, signal)

# 简单文字标注
max_idx = np.argmax(signal)
plt.text(t[max_idx], signal[max_idx], f"  Peak: {signal[max_idx]:.2f}V", fontsize=10)

# 带箭头的标注
plt.annotate("Max", xy=(t[max_idx], signal[max_idx]),
             xytext=(t[max_idx] + 0.005, signal[max_idx] - 0.3),
             arrowprops=dict(arrowstyle="->"))

plt.title("Signal with Annotations")
plt.xlabel("Time (s)")
plt.ylabel("Amplitude")
plt.grid(True)
plt.show()
```

区别：

```text
text()       在坐标位置直接放文字
annotate()   可以加箭头，指向具体点
```

场景：标信号峰值、标异常数据点、在图上直接写模型名称。

---

## 二十二、双 Y 轴 `twinx()`

同一张图，左右两个 y 轴——同时画电压和电流、loss 和 accuracy 共用 x 轴：

```python
import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 0.1, 1000)
voltage = np.sin(2 * np.pi * 50 * t)
current = 0.5 * np.sin(2 * np.pi * 50 * t + 0.5)

fig, ax1 = plt.subplots(figsize=(10, 4))

ax1.plot(t, voltage, "b-", label="Voltage")
ax1.set_xlabel("Time (s)")
ax1.set_ylabel("Voltage (V)", color="b")
ax1.tick_params(axis="y", labelcolor="b")

ax2 = ax1.twinx()                      # 共享 x 轴，创建右侧 y 轴
ax2.plot(t, current, "r--", label="Current")
ax2.set_ylabel("Current (A)", color="r")
ax2.tick_params(axis="y", labelcolor="r")

plt.title("Voltage and Current")
plt.show()
```

核心是 `ax2 = ax1.twinx()`，之后 `ax1` 和 `ax2` 各自独立控制 y 轴。

---

## 二十三、样式主题 `style.use()`

一行切换全图风格：

```python
import matplotlib.pyplot as plt

plt.style.use("ggplot")       # R 语言 ggplot 风格
plt.style.use("seaborn-v0_8") # Seaborn 风格
plt.style.use("fivethirtyeight")  # FiveThirtyEight 博客风格

# 查看所有可用样式
print(plt.style.available)
```

```python
import numpy as np
import matplotlib.pyplot as plt

plt.style.use("ggplot")

x = np.linspace(0, 10, 100)
plt.plot(x, np.sin(x), label="sin(x)")
plt.plot(x, np.cos(x), label="cos(x)")
plt.title("Styled Plot")
plt.legend()
plt.grid(True)
plt.show()
```

比默认样式好看很多，一行代码的事。

---

## 二十四、自定义刻度

```python
import numpy as np
import matplotlib.pyplot as plt

sensor_names = ["S1", "S2", "S3", "S4", "S5"]

data = np.random.rand(5, 3)

plt.imshow(data)
plt.colorbar()
plt.yticks(np.arange(5), sensor_names)         # 行标签
plt.xticks([0, 1, 2], ["T1", "T2", "T3"])     # 列标签
plt.title("Sensor Heatmap")
plt.show()
```

---

## 二十五、AI 训练曲线可视化

**Loss 曲线**：

```python
import matplotlib.pyplot as plt

epochs = [1, 2, 3, 4, 5]
train_loss = [1.2, 0.9, 0.65, 0.5, 0.42]
val_loss = [1.3, 1.0, 0.75, 0.6, 0.58]

plt.plot(epochs, train_loss, marker="o", label="Train Loss")
plt.plot(epochs, val_loss, marker="s", label="Validation Loss")

plt.title("Loss Curve")
plt.xlabel("Epoch")
plt.ylabel("Loss")
plt.legend()
plt.grid(True)
plt.show()
```

看曲线时的常见判断：

```text
train 和 val 都下降       → 训练正常
train 下降但 val 上升     → 可能过拟合
长时间不下降              → 学习率或模型有问题
```

**Accuracy 曲线**：

```python
epochs = [1, 2, 3, 4, 5]
train_acc = [0.55, 0.68, 0.76, 0.83, 0.88]
val_acc = [0.52, 0.64, 0.72, 0.78, 0.80]

plt.plot(epochs, train_acc, marker="o", label="Train Accuracy")
plt.plot(epochs, val_acc, marker="s", label="Validation Accuracy")

plt.title("Accuracy Curve")
plt.xlabel("Epoch")
plt.ylabel("Accuracy")
plt.ylim(0, 1)
plt.legend()
plt.grid(True)
plt.show()
```

---

## 二十六、填充区域 `fill_between()`

信号处理和 AI 里极其常见——画置信区间、误差带、包络：

```python
import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 0.1, 1000)
signal = np.sin(2 * np.pi * 50 * t)

plt.plot(t, signal)
plt.fill_between(t, signal - 0.1, signal + 0.1, alpha=0.3, label="±0.1 band")
plt.title("Signal with Confidence Band")
plt.xlabel("Time (s)")
plt.ylabel("Amplitude")
plt.legend()
plt.grid(True)
plt.show()
```

训练曲线带方差阴影也是用 `fill_between`。`alpha` 控制填充区的透明程度。

---

## 二十七、误差可视化

真实值 vs 预测值：

```python
import numpy as np
import matplotlib.pyplot as plt

y_true = np.array([3.0, 3.2, 3.4, 3.6, 3.8])
y_pred = np.array([3.1, 3.1, 3.5, 3.4, 3.9])
error = y_pred - y_true

# 子图 1：对比
plt.subplot(2, 1, 1)
plt.plot(y_true, marker="o", label="True")
plt.plot(y_pred, marker="s", label="Predicted")
plt.title("True vs Predicted")
plt.legend()
plt.grid(True)

# 子图 2：误差
plt.subplot(2, 1, 2)
plt.bar(range(len(error)), error)
plt.title("Prediction Error")
plt.xlabel("Sample")
plt.ylabel("Error")

plt.tight_layout()
plt.show()
```

---

## 二十八、常见错误

| 错误 | ✅ 正确做法 |
|------|-----------|
| 脚本里图不显示 | 加 `plt.show()` |
| x 和 y 长度不等 | 检查两边 shape |
| 写了 label 但图例不出现 | 加 `plt.legend()` |
| 中文变方块 | 设置 `rcParams` 或直接用英文 |
| 保存的是空白图 | `savefig` 放 `show()` 前面 |
| 多张图画一起互相覆盖 | 每张图前加 `plt.figure()` |
| 多图标题重叠 | 加 `plt.tight_layout()` |

---

## 二十九、学习主线

```text
 1. plt.plot 画折线图（信号波形）
 2. plt.title / xlabel / ylabel / legend / grid 加标注
 3. 画多条曲线
 4. plt.xlim / plt.ylim 看局部
 5. plt.scatter 看样本分布
 6. plt.bar 比较模型或传感器
 7. plt.hist 看数据分布
 8. plt.imshow 看矩阵和图片
 9. plt.subplot / plt.subplots 组织多张图
10. plt.savefig 保存图片
11. 画训练 loss / accuracy 曲线
12. 画信号波形和带噪声信号
```

---

## 三十、函数速查

```text
画图：
  plt.plot(x, y)          折线图
  plt.scatter(x, y)       散点图
  plt.bar(x, y)           柱状图
  plt.hist(data, bins)    直方图
  plt.boxplot(data)       箱线图
  plt.imshow(matrix)      矩阵 / 图片

标注：
  plt.title()             标题
  plt.xlabel()            x 轴标签
  plt.ylabel()            y 轴标签
  plt.legend()            图例
  plt.grid(True)          网格
  plt.colorbar()          颜色条

范围 / 刻度：
  plt.xlim(a, b)          x 轴范围
  plt.ylim(a, b)          y 轴范围
  plt.xticks()            x 轴刻度
  plt.yticks()            y 轴刻度

辅助线：
  plt.axhline(y)          水平线
  plt.axvline(x)          竖直线

保存 / 显示：
  plt.savefig("file.png", dpi=300, bbox_inches="tight")
  plt.show()
```

---

## 三十一、现在掌握到什么程度就够了

不用背参数，这 10 项熟练就行：

```text
1. plt.plot 能画信号
2. plt.scatter 能画样本分布
3. plt.bar 能比较结果
4. plt.hist 能看数据分布
5. plt.imshow 能看矩阵和图片
6. title / xlabel / ylabel / legend / grid 会加
7. xlim / ylim 会控制显示范围
8. savefig 会保存图片
9. 能配合 NumPy 画正弦信号
10. 能画 AI 的 loss 和 accuracy 曲线
```

---

## 三十二、最重要的三个综合例子

### 例子 1：正弦信号

```python
import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 0.1, 1000)
signal = np.sin(2 * np.pi * 50 * t)

plt.plot(t, signal)
plt.title("50 Hz Sine Signal")
plt.xlabel("Time (s)")
plt.ylabel("Amplitude")
plt.grid(True)
plt.show()
```

### 例子 2：带噪声信号

```python
import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 0.1, 1000)
clean = np.sin(2 * np.pi * 50 * t)
noise = 0.2 * np.random.randn(1000)
noisy = clean + noise

plt.plot(t, clean, label="Clean")
plt.plot(t, noisy, label="Noisy")
plt.title("Signal with Noise")
plt.xlabel("Time (s)")
plt.ylabel("Amplitude")
plt.legend()
plt.grid(True)
plt.show()
```

### 例子 3：训练曲线

```python
import matplotlib.pyplot as plt

epochs = [1, 2, 3, 4, 5]
train_loss = [1.2, 0.9, 0.65, 0.5, 0.42]
val_loss = [1.3, 1.0, 0.75, 0.6, 0.58]

plt.plot(epochs, train_loss, marker="o", label="Train Loss")
plt.plot(epochs, val_loss, marker="s", label="Validation Loss")
plt.title("Loss Curve")
plt.xlabel("Epoch")
plt.ylabel("Loss")
plt.legend()
plt.grid(True)
plt.show()
```

---

> 相关笔记：
> - [[Python基础知识体系]] — Python 语法
> - [[NumPy系统知识]] — 数组计算（Matplotlib 的输入源）
> - [[Python实践串讲：从零搭建传感器系统]] — 动手项目
