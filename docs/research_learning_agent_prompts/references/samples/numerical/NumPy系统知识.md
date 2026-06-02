---
tags:
  - python/numpy
  - python/datascience
  - signal-processing
created: 2026-05-12
---

# NumPy 系统笔记

> NumPy 是 Python 科学计算的基础库。把列表变成**数学对象**——可以整体加减乘除、做统计、做矩阵运算。

核心对象只有一个：

```python
ndarray   # N 维数组
```

它解决三个问题：

```text
1. 普通列表不方便做数学计算
2. 大量数据用 for 循环处理效率低
3. AI、信号处理、电路数据本质上都是数组 / 矩阵
```

---

## 一、导入 NumPy

标准写法：

```python
import numpy as np
```

以后所有 NumPy 功能都通过 `np` 调用：

```python
np.array()
np.mean()
np.sin()
np.linspace()
```

---

## 二、ndarray — NumPy 数组

### 2.1 创建数组

```python
import numpy as np

data = np.array([1, 2, 3, 4])
print(data)   # [1 2 3 4]
```

> NumPy 数组打印时中间通常**没有逗号**（和普通列表不同）。

### 2.2 list 和 ndarray 的区别 — 最关键的一点

```python
# 普通列表
data = [1, 2, 3]
print(data * 2)    # [1, 2, 3, 1, 2, 3] — 复制了一份

# NumPy 数组
data = np.array([1, 2, 3])
print(data * 2)    # [2 4 6] — 每个元素都乘了 2
```

```text
list 更像"容器"——适合存东西
ndarray 更像"数学对象"——适合算东西
```

---

## 三、数组的基本属性

```python
data = np.array([1, 2, 3, 4])

print(data.shape)   # (4,)  — 形状
print(data.ndim)    # 1     — 维度数量
print(data.dtype)   # int64 — 元素数据类型
print(data.size)    # 4     — 元素总个数
```

二维例子：

```python
matrix = np.array([
    [1, 2, 3],
    [4, 5, 6]
])

print(matrix.shape)   # (2, 3) — 2 行 3 列
print(matrix.ndim)    # 2
print(matrix.size)    # 6
```

---

## 四、创建数组的常用方法

### 4.1 `np.array()` — 手动创建

```python
data = np.array([1, 2, 3])

matrix = np.array([
    [1, 2, 3],
    [4, 5, 6]
])
```

### 4.2 `np.arange()` — 类似 range()

```python
np.arange(0, 10, 2)    # [0 2 4 6 8]
# np.arange(start, stop, step)，不包括 stop
```

### 4.3 `np.linspace()` — 均匀分布点（信号处理核心）

```python
t = np.linspace(0, 1, 5)    # [0. 0.25 0.5 0.75 1.]
# 从 0 到 1，均匀取 5 个点
```

生成 1 秒内的 1000 个采样点：

```python
t = np.linspace(0, 1, 1000)
```

### 4.4 `np.zeros()` / `np.ones()` / `np.full()` — 填充数组

```python
np.zeros(5)          # [0. 0. 0. 0. 0.]
np.zeros((2, 3))     # 2×3 全零矩阵
np.ones(5)           # [1. 1. 1. 1. 1.]
np.ones((2, 3))      # 2×3 全一矩阵
np.full(5, 3.3)      # [3.3 3.3 3.3 3.3 3.3]
np.full((2, 3), 7)   # 2×3 全 7 矩阵
```

### 4.5 `np.eye()` — 单位矩阵

```python
I = np.eye(3)
# [[1. 0. 0.]
#  [0. 1. 0.]
#  [0. 0. 1.]]
```

线代和 ML 里高频出现。

### 4.6 指定数据类型 `dtype` — 防止精度丢失

创建数组时**显式指定类型**，可以避免很多隐性 bug：

```python
# 创建时指定 dtype
data = np.array([1, 2, 3], dtype=np.float64)     # 64 位浮点数
data = np.array([1, 2, 3], dtype=np.int32)        # 32 位整数
data = np.array([1.0, 2.0, 3.0], dtype=np.float32) # 32 位浮点数（省内存）

# 转换已有数组的类型
data = np.array([1, 2, 3])            # dtype 自动推断为 int64
data = data.astype(np.float64)        # 转成 float64
```

**为什么重要？** 如果你创建了一个整数数组，往里面写小数会被**静默截断**：

```python
data = np.array([1, 2, 3], dtype=np.int64)
data[0] = 0.5
print(data)   # [0 2 3] — 0.5 被截成 0，不报错！
```

常见 dtype：

| dtype | 含义 | 适用场景 |
|-------|------|---------|
| `np.int32` | 32 位整数 | 标签、计数 |
| `np.int64` | 64 位整数 | 默认整数 |
| `np.float32` | 32 位浮点 | 深度学习（省显存） |
| `np.float64` | 64 位浮点 | 科学计算、信号处理 |
| `np.complex64` / `np.complex128` | 复数 | 信号频域分析 |
| `np.bool_` | 布尔值 | mask 数组 |

---

## 五、数组基本运算 — 向量化计算

### 5.1 数组和数字（广播）

```python
data = np.array([1, 2, 3])

print(data + 1)     # [2 3 4]
print(data - 1)     # [0 1 2]
print(data * 2)     # [2 4 6]
print(data / 2)     # [0.5 1.  1.5]
print(data ** 2)    # [1 4 9]
```

对每个元素做同样运算，这叫**向量化计算**。

### 5.2 数组和数组 — 逐元素运算

```python
a = np.array([1, 2, 3])
b = np.array([10, 20, 30])

print(a + b)   # [11 22 33]
print(a * b)   # [10 40 90]
```

对应位置逐个元素计算。

### 5.3 电路例子

```python
# I = U / R
voltages = np.array([3.0, 5.0, 9.0, 12.0])
resistance = 100
currents = voltages / resistance
print(currents)   # [0.03 0.05 0.09 0.12]

# P = U * I
voltages = np.array([3.0, 5.0, 9.0])
currents = np.array([0.01, 0.02, 0.03])
powers = voltages * currents
print(powers)     # [0.03 0.1  0.27]
```

---

## 六、常用数学函数

NumPy 的数学函数可以直接作用于整个数组：

```python
np.sqrt(data)    # 开方
np.abs(data)     # 绝对值
np.sin(data)     # 正弦
np.cos(data)     # 余弦
np.exp(data)     # e 的指数
np.log(data)     # 自然对数
```

例子：

```python
angles = np.array([0, np.pi / 2, np.pi])
print(np.sin(angles))  # [0. 1. 0.]
```

---

## 七、信号处理核心：生成正弦信号

正弦信号公式：

$$
x(t)=A\sin(2\pi ft)
$$

代码：

```python
import numpy as np

A = 1.0         # 幅值
f = 50          # 频率 (Hz)
t = np.linspace(0, 0.1, 1000)     # 时间轴

signal = A * np.sin(2 * np.pi * f * t)
```

关键点：

```text
t 是包含 1000 个时间点的数组
np.sin(2 * np.pi * f * t) 对每个时间点都算一次正弦值
结果 signal 也是 1000 个点的数组
```

---

## 八、统计函数

```python
voltages = np.array([3.1, 3.2, 3.3, 3.4, 3.5])

np.max(voltages)      # 3.5  最大值
np.min(voltages)      # 3.1  最小值
np.mean(voltages)     # 3.3  平均值
np.sum(voltages)      # 16.5 总和
np.std(voltages)      # 标准差
np.var(voltages)      # 方差
np.argmax(voltages)   # 最大值所在索引
np.argmin(voltages)   # 最小值所在索引
```

例子：

```python
data = np.array([3.1, 3.5, 3.2, 3.8])
print(np.argmax(data))   # 3  — 3.8 在索引 3
print(np.argmin(data))   # 0  — 3.1 在索引 0
```

### 排序 `sort()` / `argsort()`

```python
data = np.array([3.5, 3.1, 3.8, 3.2])

sorted_data = np.sort(data)         # [3.1 3.2 3.5 3.8] — 排序后的值
indices = np.argsort(data)          # [1 3 0 2] — 排序后的索引位置
# 意思是：最小的值在索引 1（3.1），然后是索引 3（3.2），索引 0（3.5），索引 2（3.8）
```

信号处理里找峰值、AI 里取 Top-K 预测结果都离不开排序。

### 找唯一值 `np.unique()`

```python
labels = np.array([0, 1, 1, 0, 2, 1, 2])

print(np.unique(labels))                           # [0 1 2]
print(np.unique(labels, return_counts=True))       # (array([0,1,2]), array([2,3,2]))
# 0 出现 2 次，1 出现 3 次，2 出现 2 次
```

处理分类标签、去重统计时非常高频。

---

## 九、二维数组和 axis

### 9.1 理解 axis

```python
data = np.array([
    [1, 2, 3],
    [4, 5, 6]
])
# shape = (2, 3) → 2 行 3 列
```

```python
print(np.sum(data, axis=0))    # [5 7 9] — 按列求和
# axis=0: 压缩行，保留列（每列得到一个结果）

print(np.sum(data, axis=1))    # [6 15] — 按行求和
# axis=1: 压缩列，保留行（每行得到一个结果）
```

### 9.2 记忆法

```text
axis=0：沿第 0 个轴（行方向）操作 → 每列一个结果
axis=1：沿第 1 个轴（列方向）操作 → 每行一个结果
```

### 9.3 实战例子：学生成绩

```python
scores = np.array([
    [80, 90, 85],    # 学生 1 的三门课
    [70, 75, 80],    # 学生 2
    [90, 95, 92]     # 学生 3
])

# 每个学生的平均分（按行求）
student_mean = np.mean(scores, axis=1)

# 每门课的平均分（按列求）
course_mean = np.mean(scores, axis=0)
```

### 9.4 `keepdims` 参数 — 保留被压缩的维度

`axis` 操作后默认会**丢掉一个维度**，shape 从 `(2,3)` 变成 `(3,)`。广播时经常因为 shape 不匹配报错。

加 `keepdims=True` 可以保留这个维度：

```python
data = np.array([
    [1, 2, 3],
    [4, 5, 6]
])

mean = np.mean(data, axis=0)                      # shape (3,)
mean_kd = np.mean(data, axis=0, keepdims=True)    # shape (1, 3) — 保留了行维度
```

区别：

```python
print(data - mean)        # ✅ 碰巧能工作（广播自动补了）
print(data - mean_kd)     # ✅ 显式保留了维度，广播意图更清晰
```

**什么时候该用？** 当你对二维数组做 axis 操作后还要和原数组做运算时，加 `keepdims=True` 最安全。

---

## 十、索引和切片

### 10.1 一维数组

```python
data = np.array([10, 20, 30, 40, 50])

print(data[0])       # 10
print(data[-1])      # 50
print(data[1:4])     # [20 30 40]
print(data[:3])      # [10 20 30]
print(data[2:])      # [30 40 50]
```

### 10.2 二维数组

```python
matrix = np.array([
    [1, 2, 3],
    [4, 5, 6]
])

print(matrix[0, 0])      # 1 — matrix[行, 列]
print(matrix[1, 2])      # 6

print(matrix[0, :])      # [1 2 3] — 取整行（等价于 matrix[0]）
print(matrix[:, 1])      # [2 5]   — 取整列

# 子矩阵
matrix = np.array([
    [1, 2, 3],
    [4, 5, 6],
    [7, 8, 9]
])
print(matrix[0:2, 1:3])  # [[2 3]
                          #  [5 6]]
```

> 仍然是左闭右开。

---

## 十一、布尔索引

### 11.1 基础用法

```python
voltages = np.array([3.1, 3.5, 3.2, 3.8])

mask = voltages > 3.3
print(mask)   # [False  True False  True]

over_limit = voltages[mask]
print(over_limit)   # [3.5 3.8]
```

简写（推荐）：

```python
over_limit = voltages[voltages > 3.3]
```

### 11.2 多条件筛选

```python
data = np.array([3.1, 3.3, 3.5, 3.8])

selected = data[(data >= 3.2) & (data <= 3.6)]
print(selected)   # [3.3 3.5]
```

> ⚠️ NumPy 里用 `&` / `|` / `~`，**不用** Python 的 `and` / `or` / `not`。
> 
> 每个条件**必须用括号**包起来！

### 11.3 `np.where()` — 向量化的 if-else

```python
voltages = np.array([3.1, 3.5, 3.2, 3.8, 2.9])

# 按条件选择输出
result = np.where(voltages > 3.3, "超限", "正常")
print(result)   # ['正常' '超限' '正常' '超限' '正常']

# 数值裁剪：超限的值替换为 3.3，没超的保留原值
clipped = np.where(voltages > 3.3, 3.3, voltages)
print(clipped)  # [3.1 3.3 3.2 3.3 2.9]
```

等于把 `if-else` 搬到了数组上，比布尔索引更灵活——可以选择"满足条件输出什么，不满足输出什么"。

---

## 十二、花式索引

用索引列表一次取多个位置：

```python
data = np.array([10, 20, 30, 40, 50])
selected = data[[0, 2, 4]]
print(selected)   # [10 30 50]
```

二维：

```python
matrix = np.array([
    [1, 2],
    [3, 4],
    [5, 6]
])
print(matrix[[0, 2]])   # 第 0 行和第 2 行
```

---

## 十三、改变数组形状

### 13.1 `reshape()`

```python
data = np.array([1, 2, 3, 4, 5, 6])
matrix = data.reshape(2, 3)
# [[1 2 3]
#  [4 5 6]]
```

元素总数必须对得上。

### 13.2 `reshape(-1, ...)` — 自动推断

```python
data = np.array([1, 2, 3, 4, 5, 6])
matrix = data.reshape(-1, 2)
# [[1 2]
#  [3 4]
#  [5 6]]
# 每行 2 个，行数自动算
```

AI 里常见用法（sklearn 需要二维输入）：

```python
x = np.array([1, 2, 3, 4])     # (4,)
x = x.reshape(-1, 1)           # (4, 1) — 变 4 行 1 列
```

### 13.3 展平 `flatten()`

```python
matrix = np.array([
    [1, 2, 3],
    [4, 5, 6]
])
data = matrix.flatten()
print(data)   # [1 2 3 4 5 6]
```

### 13.4 转置 `.T`

```python
matrix = np.array([
    [1, 2, 3],
    [4, 5, 6]
])
print(matrix.T)
# [[1 4]
#  [2 5]
#  [3 6]]
# 原来是 (2,3)，转置后 (3,2)
```

### 13.5 拼接数组 `concatenate` / `vstack` / `hstack`

把多个数组合并成一个，实际项目中非常高频：

```python
a = np.array([1, 2, 3])
b = np.array([4, 5, 6])

np.concatenate([a, b])       # [1 2 3 4 5 6]  首尾相接
np.vstack([a, b])            # [[1 2 3]        竖着堆叠（每个数组变成一行）
                              #  [4 5 6]]
np.hstack([a, b])            # [1 2 3 4 5 6]  横着拼接
```

二维拼接：

```python
A = np.array([[1, 2], [3, 4]])
B = np.array([[5, 6], [7, 8]])

np.vstack([A, B])            # 上下堆：shape (4, 2)
np.hstack([A, B])            # 左右拼：shape (2, 4)
```

AI 场景里，遍历 N 个 batch 拿到的数据，经常需要用 `np.vstack` 叠成一个完整矩阵。

---

## 十四、矩阵运算

### 14.1 逐元素乘法 `*`

```python
a = np.array([1, 2, 3])
b = np.array([10, 20, 30])
print(a * b)   # [10 40 90] — 对应位置相乘
```

### 14.2 点积 `np.dot()` 和矩阵乘法 `@`

```python
# 向量点积
a = np.array([1, 2, 3])
b = np.array([10, 20, 30])
print(np.dot(a, b))   # 140  (1*10 + 2*20 + 3*30)

# 矩阵乘法
A = np.array([[1, 2],
              [3, 4]])
B = np.array([[10, 20],
              [30, 40]])
C = A @ B    # 等价 np.matmul(A, B)
```

| 运算符 | 含义 |
|--------|------|
| `A * B` | 逐元素乘法 |
| `A @ B` | 矩阵乘法（线性代数） |

> 在神经网络里核心公式就是：`Y = X @ W + b`

---

## 十五、广播机制 broadcasting

当两个数组形状不完全一样时，NumPy 会尝试自动**扩展较小的数组**。

### 15.1 数字被广播

```python
data = np.array([1, 2, 3])
print(data + 10)   # [11 12 13]
# 10 被扩展成 [10 10 10]
```

### 15.2 一维加到二维每一行

```python
matrix = np.array([
    [1, 2, 3],
    [4, 5, 6]
])
bias = np.array([10, 20, 30])
print(matrix + bias)
# [[11 22 33]
#  [14 25 36]]
# bias 被加到每一行上
```

AI 里就是 `Y = XW + b`，`b` 通过广播加到每个样本上。

### 15.3 形状不兼容时报错

```python
a = np.array([1, 2, 3])    # (3,)
b = np.array([10, 20])     # (2,)
a + b   # ❌ 报错，形状不对
```

---

## 十六、随机数 np.random

### 16.1 几种常用生成方式

```python
np.random.rand(5)            # 0~1 均匀分布，5 个
np.random.rand(2, 3)         # 2×3 矩阵
np.random.randn(5)           # 正态分布，均值约 0，标准差约 1
np.random.randint(0, 3, 10)  # 0~2 随机整数，10 个
```

### 16.2 固定随机种子

```python
np.random.seed(42)
data = np.random.rand(3)
# 每次运行结果一样，保证实验可复现
```

---

## 十七、信号加噪声 — 经典例子

```python
import numpy as np

A = 1.0
f = 50
t = np.linspace(0, 0.1, 1000)

clean_signal = A * np.sin(2 * np.pi * f * t)       # 干净信号
noise = 0.1 * np.random.randn(1000)                 # 随机噪声
noisy_signal = clean_signal + noise                  # 带噪信号
```

这是信号处理里的经典数据生成方式。

---

## 十八、复制和视图 — 容易踩的坑

### 18.1 直接赋值不是复制

```python
a = np.array([1, 2, 3])
b = a
b[0] = 100
print(a)   # [100   2   3] — a 也被改了
```

### 18.2 真复制用 `.copy()`

```python
a = np.array([1, 2, 3])
b = a.copy()
b[0] = 100
print(a)   # [1 2 3] — 不受影响
```

### 18.3 切片可能是视图

```python
a = np.array([1, 2, 3, 4])
b = a[1:3]
b[0] = 100
print(a)   # [1 100 3 4] — 原数组也被改了
```

想安全就加 `.copy()`：`b = a[1:3].copy()`

---

## 十九、缺失值和特殊值

```python
np.nan    # 缺失值 / 非数字
np.inf    # 正无穷
-np.inf   # 负无穷
```

```python
data = np.array([1.0, 2.0, np.nan, 4.0])
print(np.mean(data))          # nan — 有 nan 结果就是 nan
print(np.nanmean(data))       # 2.333... — 忽略 nan 求平均
```

常用 nan 系列函数：

```python
np.isnan(data)      # 判断哪些位置是 nan
np.nanmean(data)    # 忽略 nan 求平均
np.nanmax(data)     # 忽略 nan 求最大
np.nanmin(data)     # 忽略 nan 求最小
```

---

## 二十、保存和读取 NumPy 数据

```python
# 保存为 .npy（NumPy 二进制格式）
np.save("data.npy", data)
loaded = np.load("data.npy")

# 保存为文本
np.savetxt("data.txt", data)
loaded = np.loadtxt("data.txt")

# 读取 CSV
data = np.loadtxt("sensor.csv", delimiter=",")
```

> 复杂表格数据后面用 Pandas 更方便。

---

## 二十一、AI 方向核心概念：X 和 y

在机器学习里，数据的标准表示：

```python
X = np.array([
    [50, 1.0],     # 样本 1：[频率, 幅值]
    [100, 0.8],    # 样本 2
    [200, 0.5]     # 样本 3
])
y = np.array([0, 1, 1])

print(X.shape)   # (3, 2) — 3 个样本，每个样本 2 个特征
print(y.shape)   # (3,)  — 3 个标签
```

```text
X：特征矩阵（样本数 × 特征数）
y：标签数组
```

以后看到 `X_train`、`y_train`、`X_test`、`y_test` 就知道：

```text
X_train: 训练集特征
y_train: 训练集标签
X_test:  测试集特征
y_test:  测试集标签
```

---

## 二十二、数据标准化

标准化公式：

$$
z = \frac{x - \mu}{\sigma}
$$

$\mu$ 是均值，$\sigma$ 是标准差。

```python
data = np.array([10, 20, 30, 40, 50])
mean = np.mean(data)
std = np.std(data)
normalized = (data - mean) / std
```

### 二维特征矩阵标准化

```python
X = np.array([
    [50, 1.0],
    [100, 0.8],
    [200, 0.5]
])

mean = np.mean(X, axis=0)    # 每列的均值
std = np.std(X, axis=0)      # 每列的标准差
X_norm = (X - mean) / std    # 对每列分别标准化
```

`axis=0` 按列统计——这是 ML 里最常见的数据预处理操作。

---

## 二十三、常见错误集中记

| 错误 | ✅ 正确写法 |
|------|-----------|
| 忘记 import | `import numpy as np` |
| list 当 ndarray 用 | `np.array([1,2,3]) * 2` |
| shape 不匹配相加 | 检查两边 shape |
| `*` 当成矩阵乘法 | 矩阵乘法用 `@` |
| 多条件不加括号 | `(data > 3.2) & (data < 3.6)` |
| 用 `and` / `or` | 用 `&` / `\|` |
| 切片后改值影响原数组 | 加 `.copy()` |

---

## 二十四、学习主线速览

```text
 1. ndarray 是什么
 2. 怎么创建数组（array / arange / linspace / zeros / ones / eye）
 3. shape / ndim / dtype / size 怎么看
 4. 数组如何整体运算（向量化计算）
 5. 怎么做统计（mean / max / min / std / argmax）
 6. 怎么索引、切片、筛选（布尔索引、花式索引）
 7. 二维数组怎么理解（axis=0 / axis=1）
 8. reshape / flatten / .T 怎么改形状
 9. * 和 @ 的区别
10. broadcasting 怎么理解
11. np.random 怎么生成模拟数据
12. copy vs view（赋值/切片后加 .copy()）
13. 实战：生成信号、处理传感器数据、整理 AI 特征矩阵
```

---

## 二十五、综合例子合集

### 25.1 传感器数据分析

```python
import numpy as np

voltages = np.array([3.1, 3.5, 3.2, 3.8, 3.3])

mean_v = np.mean(voltages)
max_v = np.max(voltages)
min_v = np.min(voltages)
over_limit = voltages[voltages > 3.3]

print(f"平均: {mean_v}V, 最大: {max_v}V, 最小: {min_v}V")
print(f"超限: {over_limit}")
```

> 用到了：创建、统计、布尔索引、f-string

### 25.2 正弦信号生成

```python
import numpy as np

A = 1.0
f = 50
t = np.linspace(0, 0.1, 1000)
signal = A * np.sin(2 * np.pi * f * t)

print(t.shape)      # (1000,)
print(signal[:10])  # 前 10 个采样点
```

> 用到了：linspace、向量化运算、shape、切片

### 25.3 机器学习特征矩阵

```python
import numpy as np

X = np.array([
    [50, 1.0],
    [100, 0.8],
    [200, 0.5],
    [300, 0.3]
])
y = np.array([0, 0, 1, 1])

print(X.shape)          # (4, 2)
print(y.shape)          # (4,)

mean = np.mean(X, axis=0)
std = np.std(X, axis=0)
X_norm = (X - mean) / std

print(X_norm)
```

> 用到了：二维数组、shape、axis=0 统计、广播、标准化

---

## 二十六、现阶段掌握到什么程度就够了

不用背函数，能看懂这些就达标了：

```text
1. 看到 np.array 知道这是数组
2. 看到 shape 知道在看数据形状
3. 看到 axis=0 / axis=1 不慌
4. 知道数组可以整体加减乘除（向量化）
5. 知道布尔索引可以筛数据
6. 知道 reshape 是改形状
7. 知道 @ 是矩阵乘法
8. 能用 linspace + sin 生成信号
9. 能用 mean / std 做数据处理
10. 能看懂 X 是特征矩阵，y 是标签
```

> NumPy 真正熟练靠的是后面写项目形成直觉，不是硬背函数。

---

> 相关笔记：
> - [[Python基础知识体系]] — Python 语法地基
> - [[Python面向对象：class]] — OOP 入门
> - [[Python实践串讲：从零搭建传感器系统]] — 从零敲一个完整项目
