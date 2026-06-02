---
tags:
  - python/pytorch
  - deep-learning
  - python/advanced
created: 2026-05-18
---
 # PyTorch 系统笔记

> PyTorch 是深度学习框架。核心对象 `torch.Tensor`——可以理解为 **NumPy 数组的深度学习版本**，但它多四个关键能力：GPU 运算、自动求导、构建神经网络、参与模型训练。

对电子信息 / AI 方向，PyTorch 是从 NumPy 通往大模型的桥梁：

```text
NumPy 数组           →  PyTorch Tensor  →  nn.Module  →  Transformer  →  MiniMind
科学计算 / 信号处理         GPU + 自动求导       神经网络层       注意力机制        手写大模型
```

---

## 一、导入 PyTorch

标准写法：

```python
import torch
import torch.nn as nn
import torch.optim as optim
```

含义：

```text
torch        →  核心张量计算
torch.nn     →  神经网络模块（Linear、Embedding、Loss 等）
torch.optim  →  优化器（SGD、Adam、AdamW 等）
```

---

## 二、Tensor 是什么

Tensor 是张量，本质是多维数组。

```text
0 维：标量 scalar        torch.tensor(3.0)
1 维：向量 vector        torch.tensor([1.0, 2.0, 3.0])
2 维：矩阵 matrix        torch.tensor([[1, 2], [3, 4]])
3 维及以上：张量 tensor   torch.randn(2, 3, 4)
```

```python
import torch

a = torch.tensor(3.0)                       # 标量
b = torch.tensor([1.0, 2.0, 3.0])           # 向量
c = torch.tensor([[1.0, 2.0], [3.0, 4.0]])  # 矩阵

print(a, b, c)
```

> 和 NumPy 的区别：Tensor 可以放在 GPU 上、可以自动求导。NumPy 不行。

---

## 三、创建 Tensor

### 3.1 手动创建

```python
x = torch.tensor([1, 2, 3])
```

### 3.2 全 0 / 全 1

```python
x = torch.zeros(2, 3)    # 2 行 3 列，全是 0
x = torch.ones(2, 3)     # 2 行 3 列，全是 1
```

### 3.3 随机数

```python
x = torch.rand(2, 3)     # 0~1 均匀分布
x = torch.randn(2, 3)    # 标准正态分布（深度学习初始化参数常用）
```

### 3.4 指定范围整数

```python
x = torch.randint(0, 10, (2, 3))   # 0~9 随机整数，形状 (2, 3)
```

### 3.5 从 range 创建

```python
x = torch.arange(6)                 # tensor([0, 1, 2, 3, 4, 5])
x = torch.arange(0, 10, 2)         # tensor([0, 2, 4, 6, 8])
```

### 3.6 等间距：linspace

```python
x = torch.linspace(0, 1, steps=5)    # tensor([0.00, 0.25, 0.50, 0.75, 1.00])
```

和 `arange` 的区别：`arange` 指定步长，`linspace` 指定点数。

### 3.7 固定随机种子：manual_seed

```python
torch.manual_seed(42)                # 固定随机种子，保证每次运行结果一样
torch.cuda.manual_seed_all(42)       # 如果使用 CUDA，也固定所有 GPU 的随机种子
x = torch.randn(2, 3)                # 只要种子相同，结果就相同
```

> 学习和普通实验里，第一步先设随机种子，能让大部分随机结果稳定下来。  
> 如果追求严格复现，还要额外控制 CUDA/cuDNN 的确定性设置；不过这会牺牲一部分性能，后面做正式训练时再细看即可。

> 用到了：**torch.tensor、zeros、ones、rand、randn、randint、arange、linspace、manual_seed**

---

## 四、Tensor 的属性

```python
x = torch.randn(2, 3)

print(x.shape)    # torch.Size([2, 3])
print(x.dtype)    # torch.float32
print(x.device)   # cpu
print(x.ndim)     # 2
```

| 属性 | 含义 | 例子 |
|------|------|------|
| `shape` | 张量形状 | `torch.Size([2, 3])` |
| `dtype` | 数据类型 | `torch.float32` |
| `device` | 在 CPU 还是 GPU | `cpu` / `cuda:0` |
| `ndim` | 维度数 | `2` |

---

## 五、dtype 数据类型

常见类型：

| dtype | 含义 | 使用场景 |
|-------|------|---------|
| `torch.float32` | 32 位浮点 | **最常用**，神经网络权重默认类型 |
| `torch.float16` | 16 位半精度 | 大模型训练节省显存 |
| `torch.bfloat16` | BF16 | 大模型常用（动态范围大） |
| `torch.int64` / `torch.long` | 64 位整数 | token id、分类标签 |
| `torch.int32` | 32 位整数 | 一般整数 |
| `torch.bool` | 布尔 | mask |

```python
x = torch.tensor([1, 2, 3], dtype=torch.float32)
print(x.dtype)          # torch.float32

# 转换类型
x = x.long()            # → int64
x = x.float()           # → float32
x = x.half()            # → float16
```

重点记：

```text
模型输入的 token id  →  long / int64
神经网络权重         →  float32 / float16 / bfloat16
CrossEntropyLoss 标签 →  long
```

---

## 六、device：CPU 和 GPU

PyTorch 可以把 Tensor 放在 CPU 或 GPU 上。

```python
device = "cuda" if torch.cuda.is_available() else "cpu"
```

创建后放到 device：

```python
x = torch.randn(2, 3).to(device)
model = model.to(device)
```

> ⚠️ 训练时，**数据和模型必须在同一个 device 上**。模型在 GPU、数据在 CPU → 报错。

标准模板：

```python
device = "cuda" if torch.cuda.is_available() else "cpu"

x = x.to(device)
y = y.to(device)
model = model.to(device)
```

---

## 七、Tensor 基本运算

和 NumPy 几乎一样，都是**逐元素**运算：

```python
x = torch.tensor([1.0, 2.0, 3.0])

print(x + 1)     # tensor([2., 3., 4.])
print(x * 2)     # tensor([2., 4., 6.])
print(x ** 2)    # tensor([1., 4., 9.])
```

两个 Tensor 运算：

```python
a = torch.tensor([1.0, 2.0, 3.0])
b = torch.tensor([10.0, 20.0, 30.0])

print(a + b)     # tensor([11., 22., 33.])
print(a * b)     # tensor([10., 40., 90.])
```

---

## 八、统计函数

```python
x = torch.tensor([1.0, 2.0, 3.0, 4.0])

print(x.mean())    # 2.5
print(x.sum())     # 10.0
print(x.max())     # 4.0
print(x.min())     # 1.0
print(x.std())     # 标准差
```

两种写法都常见：

```python
torch.mean(x)      # 函数形式
x.mean()           # 方法形式
```

---

## 九、索引和切片

和 NumPy / Python 列表语法完全一致：

```python
x = torch.tensor([10, 20, 30, 40])

print(x[0])        # 10
print(x[-1])       # 40
print(x[1:3])      # tensor([20, 30])
```

二维：

```python
matrix = torch.tensor([[1, 2, 3],
                        [4, 5, 6]])

print(matrix[0, 1])    # 2  — 第 0 行第 1 列
print(matrix[:, 1])    # tensor([2, 5])  — 所有行，第 1 列
print(matrix[1, :])    # tensor([4, 5, 6])  — 第 1 行，所有列
```

---

## 十、布尔索引

```python
x = torch.tensor([3.1, 3.5, 3.2, 3.8])

mask = x > 3.3
print(mask)              # tensor([False, True, False, True])
print(x[mask])           # tensor([3.5000, 3.8000])
```

一步到位：

```python
over_limit = x[x > 3.3]
```

---

## 十一、形状变换：reshape / view

这是 PyTorch 里**出现频率最高**的操作之一。

```python
x = torch.arange(6)           # tensor([0, 1, 2, 3, 4, 5])
y = x.reshape(2, 3)
print(y)
# tensor([[0, 1, 2],
#         [3, 4, 5]])
```

也可以用 `view`：

```python
y = x.view(2, 3)
```

区别：

```text
reshape  →  更灵活，不要求内存连续（初学优先用）
view     →  要求内存连续，否则报错
```

如果 `view` 报错，解决方式：

```python
x = x.contiguous().view(2, 3)   # 先整理内存
# 或者直接用
x = x.reshape(2, 3)              # 推荐
```

---

## 十二、`-1` 自动推断形状

```python
x = torch.arange(12)
y = x.reshape(3, -1)
print(y.shape)    # torch.Size([3, 4])
```

意思是：

```text
我指定 3 行，列数你自动算 → 12 / 3 = 4 列
```

大模型里极其常见：

```python
x = x.reshape(batch_size, seq_len, hidden_size)
logits = logits.reshape(-1, vocab_size)
```

---

## 十三、squeeze 和 unsqueeze

### 13.1 unsqueeze：增加一个维度

```python
x = torch.tensor([1, 2, 3])        # shape: (3,)

y = x.unsqueeze(0)                 # shape: (1, 3)  — 在第 0 维前插入
z = x.unsqueeze(1)                 # shape: (3, 1)  — 在第 1 维前插入
```

```text
unsqueeze(dim) → 在 dim 位置插入一个长度为 1 的维度
```

### 13.2 squeeze：去掉长度为 1 的维度

```python
x = torch.randn(1, 3, 1)          # shape: (1, 3, 1)
y = x.squeeze()                    # shape: (3,)  — 去掉所有长度为 1 的维度
z = x.squeeze(0)                   # shape: (3, 1)  — 只去掉第 0 维
```

> 用到了：**squeeze、unsqueeze、dim 参数**

---

## 十四、transpose 和 permute

### 14.1 transpose：交换两个维度

```python
x = torch.randn(2, 3)              # shape: (2, 3)
y = x.transpose(0, 1)             # shape: (3, 2)
```

### 14.2 permute：重新排列多个维度

```python
x = torch.randn(2, 3, 4)          # shape: (2, 3, 4)
y = x.permute(0, 2, 1)           # shape: (2, 4, 3)
```

```text
原维度顺序: (0, 1, 2)
permute(0, 2, 1): 第 1 和第 2 维交换
```

### 14.3 大模型里的常见 shape 变换

Attention 中把 `hidden_size` 拆成多个 head：

```python
# (batch, seq_len, hidden_size)
# → (batch, seq_len, num_heads, head_dim)
# → (batch, num_heads, seq_len, head_dim)
x = x.view(batch_size, seq_len, num_heads, head_dim)
x = x.transpose(1, 2)
```

---

## 十五、矩阵乘法

### 15.1 逐元素乘法 `*`

```python
a = torch.tensor([1, 2, 3])
b = torch.tensor([10, 20, 30])
print(a * b)    # tensor([10, 40, 90])
```

### 15.2 向量点积 `torch.dot`

```python
a = torch.tensor([1.0, 2.0, 3.0])
b = torch.tensor([10.0, 20.0, 30.0])
print(torch.dot(a, b))    # 140.0  (= 1×10 + 2×20 + 3×30)
```

### 15.3 向量外积 `torch.outer`

两个向量一列一行相乘，得到一个矩阵：

```python
a = torch.tensor([1.0, 2.0, 3.0])        # 形状 (3,)
b = torch.tensor([10.0, 20.0])           # 形状 (2,)

M = torch.outer(a, b)
print(M)
# tensor([[10., 20.],
#         [20., 40.],
#         [30., 60.]])

print(M.shape)    # torch.Size([3, 2])
```

```text
a[i] × b[j] → M[i, j]

outer( [a₁, a₂, a₃], [b₁, b₂] ) =
┌                ┐
│ a₁b₁    a₁b₂   │
│ a₂b₁    a₂b₂   │
│ a₃b₁    a₃b₂   │
└                ┘
```

```text
dot(a, b)  →  标量（一个数）   — 对应位置乘再求和
outer(a, b) → 矩阵            — 每个 a[i] 和每个 b[j] 都乘一次
```

实用场景：

```python
# 构建位置偏置矩阵（某些 Attention 变体）
positions = torch.arange(seq_len, dtype=torch.float32)
pos_bias = torch.outer(positions, positions)   # (T, T)
pos_bias = torch.abs(pos_bias - pos_bias.T)     # 两两位置差的绝对值
```

### 15.4 矩阵乘法 `@` 或 `torch.matmul`

```python
A = torch.tensor([[1.0, 2.0],
                   [3.0, 4.0]])
B = torch.tensor([[10.0, 20.0],
                   [30.0, 40.0]])

C = A @ B                    # 推荐写法
C = torch.matmul(A, B)       # 等价
```

> **`*` 是逐元素乘，`@` 是矩阵乘。这个区分非常重要。**

---

## 十六、batch 矩阵乘法

大模型里经常不是两个矩阵相乘，而是一批矩阵相乘：

```python
A = torch.randn(2, 3, 4)     # 2 组 (3, 4) 矩阵
B = torch.randn(2, 4, 5)     # 2 组 (4, 5) 矩阵

C = A @ B
print(C.shape)               # torch.Size([2, 3, 5])
```

```text
每组 (3, 4) @ (4, 5) → (3, 5)
共 2 组，所以整体是 (2, 3, 5)
```

Attention 里会看到：

```python
scores = Q @ K.transpose(-2, -1)   # batch 矩阵乘法
```

---

## 十七、广播机制 broadcasting

和 NumPy 一样：

```python
x = torch.tensor([1, 2, 3])
print(x + 10)    # tensor([11, 12, 13])  — 10 自动扩展为 [10, 10, 10]
```

二维：

```python
x = torch.tensor([[1, 2, 3],
                   [4, 5, 6]])            # (2, 3)
bias = torch.tensor([10, 20, 30])         # (3,)

print(x + bias)    # bias 加到每一行
```

大模型里：

```python
Y = X @ W + b      # b 被广播到每一行
```

---

## 十八、Tensor 和 NumPy 互转

### 18.1 NumPy → Tensor

```python
import numpy as np
import torch

arr = np.array([1, 2, 3])
x = torch.from_numpy(arr)      # 共享内存
x = torch.tensor(arr)          # 复制一份
```

### 18.2 Tensor → NumPy

```python
x = torch.tensor([1, 2, 3])
arr = x.numpy()
```

### 18.3 GPU Tensor → NumPy（先回 CPU）

```python
arr = x.cpu().numpy()
```

### 18.4 带梯度的 Tensor → NumPy

```python
arr = x.detach().cpu().numpy()
```

重点：

```text
detach()  →  从计算图分离
cpu()     →  移回 CPU
numpy()   →  转为 NumPy 数组
```

### 18.5 clone()：真的复制一份

```python
x = torch.tensor([1.0, 2.0, 3.0])
y = x                  # ❌ 这只是引用！改 y 会动 x
y = x.clone()          # ✅ 真正的复制，独立的副本
```

带梯度的 tensor：

```python
y = x.detach().clone()  # 先脱离计算图，再复制
```

> ⚠️ 这是高频坑：`y = x` 不是复制，是起别名。需要独立副本就用 `clone()`。

---

## 十九、自动求导 autograd

这是 PyTorch 区别于 NumPy 的**核心能力**。

```python
x = torch.tensor(2.0, requires_grad=True)

y = x ** 2 + 3 * x + 1     # y = x² + 3x + 1

y.backward()                # 自动计算 dy/dx

print(x.grad)               # tensor(7.)
```

验证：`dy/dx = 2x + 3`，当 `x = 2` 时，`2×2 + 3 = 7` ✓

> `requires_grad=True` 表示"追踪这个变量参与的计算，后面可以对它求导"。
>
> 神经网络里的权重参数默认都是 `requires_grad=True`。

---

## 二十、计算图与反向传播

PyTorch 会记录 Tensor 的计算过程：

```text
x ──→ y = x * 3 ──→ z = y²
```

调用 `z.backward()` 时，PyTorch 从 z 反向计算每个变量的梯度。这就是**反向传播**（backpropagation）的基础。

```python
x = torch.tensor(2.0, requires_grad=True)
y = x * 3
z = y ** 2

z.backward()
print(x.grad)    # dz/dx = dz/dy × dy/dx = 2y × 3 = 2×6×3 = 36
```

---

## 二十一、detach / no_grad / item

### 21.1 detach：从计算图分离

```python
x = torch.tensor(2.0, requires_grad=True)
y = x * 3
z = y.detach()        # z 不再追踪梯度
```

```text
detach() → 把张量从计算图里拿出来，后面不会继续追踪梯度
```

常见场景：

```python
loss_value = loss.detach().cpu().item()
arr = tensor.detach().cpu().numpy()
```

### 21.2 no_grad：临时关闭梯度追踪

```python
with torch.no_grad():
    output = model(x)      # 不记录计算图，省显存，速度快
```

推理、测试、生成文本时必须用。

纯推理时也常见：

```python
with torch.inference_mode():
    output = model(x)      # 比 no_grad 更偏向纯推理，开销更低
```

```text
no_grad          →  临时不记录梯度，适合验证、推理
inference_mode   →  更彻底的推理模式，适合不需要改 tensor 状态的纯推理
```

### 21.3 item：取出标量值

```python
x = torch.tensor(3.14)
value = x.item()           # 3.14 — Python float
```

训练时打印 loss：

```python
print(f"loss: {loss.item():.4f}")
```

---

## 二十二、nn.Module

`nn.Module` 是 PyTorch 里所有神经网络模块的基类。

你自己写模型的标准模板：

```python
import torch
import torch.nn as nn

class MyModel(nn.Module):
    def __init__(self):
        super().__init__()
        # 在这里定义网络层

    def forward(self, x):
        # 在这里定义数据怎么流动
        return x
```

```text
class 模型名(nn.Module):
    __init__  →  定义网络层（Linear、Embedding 等）
    forward   →  定义前向计算过程
```

### register_buffer：不是参数但跟模型绑定

有些张量不是可训练参数，但需要跟模型一起保存、一起在 GPU/CPU 之间移动。比如 causal mask、位置编码。

```python
class MyModel(nn.Module):
    def __init__(self):
        super().__init__()
        # 普通赋值不会进入 state_dict，也不会随 model.to(device) 移动
        # 用 register_buffer 解决
        self.register_buffer("causal_mask", torch.tril(torch.ones(128, 128)))
```

```text
普通属性 (self.xxx = tensor)  →  不会被保存、不会随 model.to() 移动
register_buffer              →  进入 state_dict、随模型移动，但不需要梯度
nn.Parameter                 →  进入 state_dict、随模型移动、需要梯度
```

> MiniMind 里的 causal mask 就是 buffer。

### model.apply()：批量初始化参数

```python
def init_weights(module):
    if isinstance(module, nn.Linear):
        torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
        if module.bias is not None:
            torch.nn.init.zeros_(module.bias)

model.apply(init_weights)     # 递归遍历所有子模块，对每个执行 init_weights
```

```text
model.apply(fn)  →  遍历 model 的每一层，对每一层调用 fn(module)
```

常见初始化：

```python
nn.init.normal_(tensor, mean=0.0, std=0.02)    # 正态分布
nn.init.xavier_uniform_(tensor)                 # Xavier 均匀分布
nn.init.kaiming_normal_(tensor)                 # Kaiming 正态分布
nn.init.zeros_(tensor)                          # 全零
nn.init.ones_(tensor)                           # 全一
```

> MiniMind 训练脚本里 `model.apply(init_weights)` 就是做这个的。

> 这和你前面学的 OOP 完全对上了：`__init__` 存结构，方法定义行为。

---

## 二十三、forward 方法

```python
class MyModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = nn.Linear(3, 1)

    def forward(self, x):
        return self.linear(x)
```

调用模型时：

```python
model = MyModel()
y = model(x)          # ✅ 推荐 — 会自动调用 forward，并处理 hook 等
```

> ⚠️ 不要手动写 `model.forward(x)`，写 `model(x)` 就行。

### nn.Sequential：顺序容器

当模型就是"一层接一层"时，不用手写 `forward`：

```python
# ❌ 啰嗦写法
class SimpleModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear1 = nn.Linear(10, 20)
        self.relu = nn.ReLU()
        self.linear2 = nn.Linear(20, 1)

    def forward(self, x):
        x = self.linear1(x)
        x = self.relu(x)
        x = self.linear2(x)
        return x

# ✅ Sequential 一行搞定
model = nn.Sequential(
    nn.Linear(10, 20),
    nn.ReLU(),
    nn.Linear(20, 1)
)
```

MiniMind 里堆叠多层 Transformer Block 时本质就是这个模式：

```python
self.layers = nn.ModuleList([TransformerBlock() for _ in range(n_layers)])
```

> `nn.Sequential` 适合简单串联，`nn.ModuleList` 适合需要自定义循环/条件的场景。

---

## 二十四、nn.Linear 全连接层

数学形式：

```text
y = xWᵀ + b
```

代码：

```python
linear = nn.Linear(in_features=3, out_features=2)
# 或简写：
linear = nn.Linear(3, 2)
```

含义：输入 3 个特征 → 输出 2 个特征。

```python
x = torch.randn(4, 3)        # 4 个样本，每个 3 个特征
linear = nn.Linear(3, 2)
y = linear(x)
print(y.shape)               # torch.Size([4, 2])
```

```text
输入 (4, 3) → Linear(3, 2) → 输出 (4, 2)
       ↑                        ↑
   4 个样本                  每个样本变成 2 个特征
```

MiniMind 里大量出现：

```text
q_proj、k_proj、v_proj、o_proj
gate_proj、up_proj、down_proj
```

这些基本都是 `nn.Linear`。

---

## 二十五、nn.Embedding 嵌入层

Embedding 把**整数 id 变成向量**。大模型输入是 token id：`[15496, 11, 995]`，模型不能直接理解整数，需要查表转为向量。

```python
embedding = nn.Embedding(num_embeddings=10000, embedding_dim=512)

input_ids = torch.tensor([1, 5, 20])       # 3 个 token
x = embedding(input_ids)
print(x.shape)                              # torch.Size([3, 512])
```

```text
3 个 token → 每个变成 512 维向量 → (3, 512)
```

batch 输入：

```python
input_ids = torch.tensor([[1, 5, 20],
                           [7, 8, 9]])     # (2, 3)

x = embedding(input_ids)
print(x.shape)                              # torch.Size([2, 3, 512])
```

```text
batch_size = 2
seq_len = 3
hidden_size = 512

→ (batch_size, seq_len, hidden_size)
```

这就是大模型里最常见的 hidden states 形状：**`(B, T, C)`**

---

## 二十六、激活函数

常见激活函数：

```python
nn.ReLU()       # max(0, x)
nn.SiLU()       # x × sigmoid(x)，LLaMA 系常用
nn.GELU()       # 高斯误差线性单元
nn.Tanh()       # 双曲正切
nn.Sigmoid()    # S 型曲线
```

用法：

```python
act = nn.SiLU()
y = act(x)
```

MiniMind / LLaMA 结构里 SwiGLU 的写法：

```python
# SwiGLU: SiLU(gate_proj(x)) * up_proj(x)
# 然后再接 down_proj
gate = silu(gate_proj(x))
up = up_proj(x)
out = down_proj(gate * up)
```

### 函数式写法：F.relu() / F.silu()

`nn.ReLU()` 是模块（需要在 `__init__` 里定义），`F.relu()` 是函数（在 `forward` 里直接调用）。两种写法都常见：

```python
import torch.nn.functional as F

# 模块写法 — 在 __init__ 中定义
self.act = nn.SiLU()
y = self.act(x)

# 函数式写法 — 在 forward 中直接调用（更简洁，MiniMind 里常见）
y = F.silu(x)
```

常用 `F` 函数：

```text
F.relu(x)      F.silu(x)      F.gelu(x)
F.tanh(x)      F.sigmoid(x)   F.softmax(x, dim=-1)
F.dropout(x, p=0.1, training=self.training)
```

> 函数式写法的优势是 `forward` 一目了然，不需要往回翻 `__init__` 找层名。

### 归一化层：LayerNorm 和 RMSNorm

Transformer 里每层都有归一化，把数据拉回稳定范围，防止数值失控。

```python
# LayerNorm — 标准 Transformer 归一化
norm = nn.LayerNorm(hidden_size)       # 对最后一维归一化
y = norm(x)                            # x: (B, T, C)，输出同形状

# RMSNorm — LLaMA / MiniMind 使用，比 LayerNorm 快
# MiniMind 里是自定义的 nn.Module
class RMSNorm(nn.Module):
    def __init__(self, dim, eps=1e-6):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = eps

    def forward(self, x):
        rms = torch.sqrt(torch.mean(x ** 2, dim=-1, keepdim=True) + self.eps)
        return x / rms * self.weight
```

```text
LayerNorm(x)  →  减均值、除标准差、再缩放
RMSNorm(x)    →  只除均方根（不減均值），计算量更小
```

> MiniMind 源码里 `RMSNorm` 就是这样的结构，不长，自己看懂它。

---

## 二十七、Loss 损失函数

损失函数衡量模型预测和真实答案的差距。

### 27.1 MSELoss：回归任务

```python
loss_fn = nn.MSELoss()

pred = torch.tensor([2.5])
target = torch.tensor([3.0])
loss = loss_fn(pred, target)    # (2.5 - 3.0)² = 0.25
```

用于预测连续值：电压、温度、房价等。

### 27.2 CrossEntropyLoss：分类任务

```python
loss_fn = nn.CrossEntropyLoss()

logits = torch.tensor([[2.0, 1.0, 0.1],   # 第 0 个样本
                        [0.2, 0.5, 2.0]])  # 第 1 个样本
labels = torch.tensor([0, 2])              # 第 0 个正确类别是 0，第 1 个是 2

loss = loss_fn(logits, labels)
```

重点：

```text
logits 形状：(batch_size, num_classes)
labels 形状：(batch_size,)  — 是类别编号，不是 one-hot
CrossEntropyLoss 内部已含 softmax，不需要手动做
```

> ⚠️ 大模型预测下一个 token 也是 CrossEntropyLoss。

---

## 二十八、logits / softmax / argmax

```text
logits   →  还没归一化的原始分数
softmax  →  把 logits 转成概率（和为 1）
argmax   →  取概率最大的类别
```

```python
logits = torch.tensor([2.0, 1.0, 0.1])

probs = torch.softmax(logits, dim=-1)    # 转成概率
print(probs)                              # tensor([0.6590, 0.2424, 0.0986])

pred_class = torch.argmax(probs)          # 预测类别
print(pred_class)                         # tensor(0)

pred_class2 = torch.argmax(logits)        # 也可直接从 logits 取
print(pred_class2)                        # tensor(0)
```

大模型生成时：

```text
logits → softmax → token 概率 → 采样 / 取最大 → 下一个 token
```

---

## 二十九、optimizer 优化器

优化器负责**根据梯度更新参数**。

```python
optimizer = torch.optim.SGD(model.parameters(), lr=0.01)     # 随机梯度下降
optimizer = torch.optim.Adam(model.parameters(), lr=0.001)   # Adam
optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)  # AdamW（大模型常用）
```

训练中的标准三步：

```python
optimizer.zero_grad()    # ① 清空旧梯度
loss.backward()          # ② 计算新梯度
optimizer.step()         # ③ 用梯度更新参数
```

```text
zero_grad()  →  PyTorch 默认梯度会累加，所以每轮训练前必须清空
backward()   →  反向传播，计算每个参数的梯度
step()       →  根据梯度更新参数值
```

---

## 三十、完整训练循环

最经典模板：

```python
for epoch in range(num_epochs):
    model.train()

    pred = model(x_train)              # ① 前向传播
    loss = loss_fn(pred, y_train)      # ② 计算损失

    optimizer.zero_grad()              # ③ 清空梯度
    loss.backward()                    # ④ 反向传播
    optimizer.step()                   # ⑤ 更新参数

    print(f"Epoch {epoch}: loss = {loss.item():.4f}")
```

```text
5 步循环：
前向 → 损失 → 清零 → 反向 → 更新
```

### 梯度裁剪 clip_grad_norm_

LLM 训练必用——防止某一步梯度过大导致参数剧烈震荡：

```python
loss.backward()
torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)   # 梯度模长超过 1.0 就缩放
optimizer.step()
```

> `_` 后缀表示 in-place 操作（直接修改，不返回新对象）。不加这行，大模型训练极容易梯度爆炸。

### lr_scheduler 学习率调度

训练过程中动态调整学习率，通常逐步衰减：

```python
from torch.optim.lr_scheduler import CosineAnnealingLR, StepLR, LambdaLR

# 余弦退火 — 大模型训练常用
scheduler = CosineAnnealingLR(optimizer, T_max=num_epochs)

# 每 step_size 个 epoch 衰减为原来的 gamma 倍
scheduler = StepLR(optimizer, step_size=30, gamma=0.1)

# 在训练循环中
for epoch in range(num_epochs):
    model.train()
    # ... 前向、损失、梯度、更新 ...

    scheduler.step()       # 每个 epoch 结束后更新学习率
    print(f"lr: {scheduler.get_last_lr()[0]:.6f}")
```

```text
常见策略：
StepLR         →  阶梯式下降（每 N 步 × 0.1）
CosineAnnealing →  余弦曲线下降（平滑，大模型首选）
LambdaLR       →  自定义衰减函数
```

> MiniMind 训练脚本里大概率有 CosineAnnealingLR。

---

## 三十一、model.train() 和 model.eval()

```python
model.train()    # 训练模式
model.eval()     # 评估 / 推理模式
```

区别主要影响 Dropout 和 BatchNorm：

```text
train 模式  →  Dropout 生效、BatchNorm 用当前 batch 统计
eval 模式   →  Dropout 不生效、BatchNorm 用全局统计
```

测试时必须：

```python
model.eval()
with torch.no_grad():
    pred = model(x_test)
```

---

## 三十二、Dropout

训练时随机丢弃一部分神经元，防止过拟合。

```python
dropout = nn.Dropout(p=0.1)    # 训练时随机置零 10%
```

```text
train 模式  →  Dropout 生效（随机丢弃）
eval 模式   →  Dropout 不生效（全量通过）
```

MiniMind 配置里 `dropout = 0.0` 表示不使用 dropout。

---

## 三十三、Dataset 和 DataLoader

数据量大时，不能一次全塞进模型，要分 batch。

```python
from torch.utils.data import TensorDataset, DataLoader

dataset = TensorDataset(X, y)                           # 包装数据
loader = DataLoader(dataset, batch_size=32, shuffle=True)  # 分批
```

训练时：

```python
for batch_x, batch_y in loader:
    pred = model(batch_x)
    loss = loss_fn(pred, batch_y)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
```

```text
batch_size  →  每次喂给模型多少样本
shuffle     →  每轮是否打乱数据（训练时一般 True）
```

---

## 三十四、保存和加载模型

### 34.1 保存参数（推荐）

```python
torch.save(model.state_dict(), "model.pt")
```

### 34.2 加载参数

```python
model = MyModel()                                # 先创建模型结构
model.load_state_dict(torch.load("model.pt"))    # 再加载参数
model.eval()                                     # 切换到推理模式
```

> 一般推荐保存 `state_dict`（参数字典），而不是整个模型对象。更灵活、跨平台兼容。

---

## 三十五、state_dict 和 parameters

### 35.1 state_dict：参数字典

```python
state = model.state_dict()    # 字典，保存所有参数

for name, param in model.state_dict().items():
    print(name, param.shape)
```

输出示例：

```text
linear.weight    torch.Size([2, 3])
linear.bias      torch.Size([2])
embedding.weight torch.Size([10000, 512])
```

### 35.2 parameters：可训练参数遍历

```python
for name, param in model.named_parameters():
    print(f"{name}: {param.shape}, requires_grad={param.requires_grad}")
```

> 看 MiniMind 源码时，用这两个方法可以快速了解模型结构。

---

## 三十六、cat 和 stack

### 36.1 cat：沿已有维度拼接

```python
a = torch.randn(2, 3)
b = torch.randn(2, 3)

c = torch.cat([a, b], dim=0)    # (4, 3) — 沿第 0 维（行）拼接
d = torch.cat([a, b], dim=1)    # (2, 6) — 沿第 1 维（列）拼接
```

生成文本时常用：

```python
input_ids = torch.cat([input_ids, next_token], dim=1)   # 追加新 token
```

### 36.2 stack：新增一个维度

```python
a = torch.tensor([1, 2, 3])
b = torch.tensor([4, 5, 6])

c = torch.stack([a, b], dim=0)    # shape: (2, 3)
```

```text
cat    →  沿已有维度拼接
stack  →  增加新维度再堆叠
```

---

## 三十七、Attention 中的 Q K V

假设 hidden_states 形状为 `(B, T, C)`：

```text
B = batch_size
T = seq_len
C = hidden_size
```

经过 Linear 投影：

```python
Q = q_proj(hidden_states)    # (B, T, C)
K = k_proj(hidden_states)    # (B, T, C)
V = v_proj(hidden_states)    # (B, T, C)
```

然后分成多个 head：

```python
# (B, T, C) → (B, T, num_heads, head_dim) → (B, num_heads, T, head_dim)
Q = Q.view(B, T, num_heads, head_dim).transpose(1, 2)
K = K.view(B, T, num_kv_heads, head_dim).transpose(1, 2)
V = V.view(B, T, num_kv_heads, head_dim).transpose(1, 2)
```

其中：

```text
head_dim = hidden_size // num_attention_heads

例如：
hidden_size = 512, num_attention_heads = 8
→ head_dim = 64
```

### GQA：KV head 数少于 Q head 数

MiniMind 使用 Grouped Query Attention（GQA），`num_kv_heads < num_heads`。K 和 V 的 head 数少，需要**复制**以匹配 Q 的 head 数：

```python
# 例如：num_heads=8, num_kv_heads=2
# K 的形状是 (B, 2, T, D)，Q 的形状是 (B, 8, T, D)
# 需要把 K 复制 4 次

num_groups = num_heads // num_kv_heads     # 8 / 2 = 4
K = K.unsqueeze(2).expand(-1, -1, num_groups, -1, -1)
K = K.reshape(B, num_heads, T, head_dim)   # (B, 8, T, D)
V = V.unsqueeze(2).expand(-1, -1, num_groups, -1, -1)
V = V.reshape(B, num_heads, T, head_dim)
```

或者更直接的写法（推荐）：

```python
K = K.repeat_interleave(num_groups, dim=1)  # (B, 2, T, D) → (B, 8, T, D)
V = V.repeat_interleave(num_groups, dim=1)
```

```text
expand            →  不复制内存，只改变视图；不适合做原地修改
repeat_interleave →  按 head 连续重复，适合 GQA 的 KV 复制
repeat            →  沿维度整体平铺，可能让 head 的分组顺序不符合 GQA 直觉
```

> 看 MiniMind 时，`repeat_kv` 函数就是干这个的。

---

## 三十八、Attention 分数计算

核心公式：

```text
Attention(Q, K, V) = softmax(QKᵀ / √d) V
```

代码：

```python
scores = Q @ K.transpose(-2, -1)       # (B, H, T, T)
scores = scores / math.sqrt(head_dim)  # 缩放
weights = torch.softmax(scores, dim=-1) # 归一化
out = weights @ V                       # (B, H, T, D)
```

shape 变化：

```text
Q:       (B, H, T, D)
Kᵀ:      (B, H, D, T)
Q @ Kᵀ:  (B, H, T, T)  ← 每个 token 对每个 token 的注意力分数
```

### einsum 写法

越来越多的实现用 `einsum`（Einstein summation）替代 `@` + `transpose`，因为**维度语义更直观**：

```python
# 等价写法
scores = Q @ K.transpose(-2, -1)                         # 传统写法
scores = torch.einsum("b h t d, b h s d -> b h t s", q, k)  # einsum 写法
```

```text
einsum 规则：
b → batch（保持不变）
h → head（保持不变）
t → target（Q 的 seq_len）
s → source（K 的 seq_len）
d → head_dim（求和维度）

"bhtd,bhsd→bhts" 的意思是：
保留 b、h 维度，t 和 s 分别来自 Q 和 K，d 维度求和（点积）
```

> `einsum` 的优势是**一眼能看出每个维度的作用**，不用在脑子里转置。

---

## 三十九、causal mask（因果遮罩）

大模型是从左到右预测的，当前 token **不能看到未来 token**。

```text
第 1 个 token 只能看第 1 个
第 2 个 token 可以看第 1、2 个
第 3 个 token 可以看第 1、2、3 个
...
```

实现：把未来位置设为 `-inf`，softmax 后概率接近 0。

### 创建 causal mask

```python
T = 8    # seq_len

# torch.tril 取矩阵的下三角（包含对角线）
mask = torch.tril(torch.ones(T, T))     # (8, 8)
# tensor([[1, 0, 0, 0, 0, 0, 0, 0],
#         [1, 1, 0, 0, 0, 0, 0, 0],
#         [1, 1, 1, 0, 0, 0, 0, 0],
#         ...
#         [1, 1, 1, 1, 1, 1, 1, 1]])

# 对齐 Attention 的 shape: (1, 1, T, T)
mask = mask.view(1, 1, T, T)
```

```text
tril   →  下三角 (lower triangular)，保留对角线及以下
triu   →  上三角 (upper triangular)，保留对角线及以上
```

### 应用 mask

```python
scores = scores.masked_fill(mask == 0, float("-inf"))
weights = torch.softmax(scores, dim=-1)
```

```text
mask == 0 的位置（未来 token）→ 设为 -inf → softmax 后 ≈ 0
mask == 1 的位置（当前及过去）→ 保留原值
```

> MiniMind 里 causal mask 通常注册为 buffer（`register_buffer`），在 `__init__` 中创建一次即可。

---

## 四十、从 Embedding 到 logits

大模型整体流程：

```text
input_ids (B, T)
    │
    ▼
Token Embedding
    │
    ▼
hidden_states (B, T, C)
    │
    ▼
× N 层 Transformer Block（Attention + FFN）
    │
    ▼
RMSNorm
    │
    ▼
lm_head (Linear: C → vocab_size)
    │
    ▼
logits (B, T, vocab_size)
```

```text
logits[b, t, :]  →  第 b 个样本、第 t 个位置、预测下一个 token 的 vocab_size 个分数
```

---

## 四十一、CrossEntropy 在大模型中的 shape

大模型训练时：

```text
logits: (B, T, vocab_size)
labels: (B, T)
```

CrossEntropyLoss 期望输入是 `(N, C)` 和 `(N,)`，所以需要 reshape：

```python
loss = loss_fn(
    logits.reshape(-1, vocab_size),    # (B*T, vocab_size)
    labels.reshape(-1)                 # (B*T,)
)
```

```text
N = B × T        （把 batch 和 seq_len 合并）
C = vocab_size
```

---

## 四十二、生成 generate 的基本思想

模型每次预测**下一个** token：

```text
输入: "Hello"
  → 预测下一个 token: " world"
  → 拼回去: "Hello world"
  → 继续预测下一个
  → ...
```

伪代码：

```python
for _ in range(max_new_tokens):
    logits = model(input_ids)                        # 前向
    next_token_logits = logits[:, -1, :]             # 取最后一个位置
    next_token = torch.argmax(next_token_logits, dim=-1, keepdim=True)
    input_ids = torch.cat([input_ids, next_token], dim=1)  # 拼接
```

```text
logits[:, -1, :]  →  只取最后一个位置的预测（因为前面已经生成过了）
```

### 温度采样 temperature

`argmax` 每次取最大概率 token → 生成结果固定、缺乏变化。加温度控制随机性：

```python
temperature = 0.8
logits = logits[:, -1, :] / temperature          # 温度 < 1 → 更确定，> 1 → 更多样
probs = torch.softmax(logits, dim=-1)
next_token = torch.multinomial(probs, num_samples=1)  # 按概率采样，不是取最大
```

```text
temperature = 0.1  →  接近 argmax，几乎确定性的输出
temperature = 0.8  →  平衡
temperature = 1.0  →  原始分布
temperature = 2.0  →  更平滑、更多样、更容易胡说
```

> `torch.multinomial` 按概率采样——概率高的 token 更容易被抽到，但不是必然。

更完整的生成还会加 top-k（只保留概率最高的 k 个 token）和 top-p（核采样），但这些是进阶内容，看到时再查即可。

---

## 四十三、完整例子：线性回归

```python
import torch
import torch.nn as nn
import torch.optim as optim

# 数据：y ≈ 2x
X = torch.tensor([[1.0], [2.0], [3.0], [4.0]])
y = torch.tensor([[2.0], [4.0], [6.0], [8.0]])

class SimpleModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = nn.Linear(1, 1)

    def forward(self, x):
        return self.linear(x)

model = SimpleModel()
loss_fn = nn.MSELoss()
optimizer = optim.SGD(model.parameters(), lr=0.01)

for epoch in range(100):
    pred = model(X)
    loss = loss_fn(pred, y)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    if epoch % 20 == 0:
        print(f"Epoch {epoch}: loss = {loss.item():.4f}")

# 测试
test = torch.tensor([[5.0]])
print(f"预测 f(5) = {model(test).item():.2f}")   # 应该接近 10.0
```

---

## 四十四、完整例子：二分类

```python
import torch
import torch.nn as nn
import torch.optim as optim

# 数据：前两个是类别 0，后两个是类别 1
X = torch.tensor([[1.0, 2.0], [1.5, 1.8], [5.0, 8.0], [6.0, 8.5]])
y = torch.tensor([0, 0, 1, 1])

class Classifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = nn.Linear(2, 2)    # 2 输入 → 2 输出（二分类）

    def forward(self, x):
        return self.linear(x)

model = Classifier()
loss_fn = nn.CrossEntropyLoss()
optimizer = optim.Adam(model.parameters(), lr=0.01)

for epoch in range(100):
    logits = model(X)
    loss = loss_fn(logits, y)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    if epoch % 20 == 0:
        pred = torch.argmax(logits, dim=1)
        acc = (pred == y).float().mean()
        print(f"Epoch {epoch}: loss={loss.item():.4f}, acc={acc.item():.2%}")
```

重点：

```text
模型输出 logits（不是概率）
CrossEntropyLoss 内部会处理 softmax
label 是类别编号（0 或 1），不是 one-hot
argmax 得到预测类别
```

---

## 四十五、常见错误集中记

### 1. Tensor 在不同 device

```text
❌ 模型在 cuda，数据在 cpu → 报错
```

```python
# ✅ 统一放同一个 device
x = x.to(device)
model = model.to(device)
```

### 2. dtype 错误

```python
input_ids = input_ids.long()     # Embedding 输入必须是整数
labels = labels.long()           # CrossEntropyLoss 标签必须是 long
```

### 3. 忘记 zero_grad

```python
# ❌ 错误 — 梯度会累加
loss.backward()
optimizer.step()

# ✅ 正确
optimizer.zero_grad()
loss.backward()
optimizer.step()
```

### 4. 训练 / 推理模式混乱

```python
model.train()                    # 训练
model.eval()                     # 推理
with torch.no_grad():            # 推理时不追梯度
    ...
```

### 5. shape 不匹配

`nn.Linear(3, 2)` 要求输入最后一维是 3：

```python
x = torch.randn(batch, 3)        # ✅
x = torch.randn(batch, seq, 3)   # ✅（Linear 只看最后一维）
x = torch.randn(batch, 5)        # ❌ 最后一维不是 3
```

### 6. CrossEntropyLoss 输入错误

```python
# ✅ 正确：传 logits，不传 softmax 结果
loss = loss_fn(logits, labels)

# ❌ 错误：先 softmax 再传
probs = torch.softmax(logits, dim=-1)
loss = loss_fn(probs, labels)    # CrossEntropyLoss 内部已有 softmax！
```

### 7. view 报错

```python
# 当内存不连续时 view 报错
x = x.contiguous().view(...)     # 先整理内存
# 或直接用
x = x.reshape(...)               # 推荐
```

---

## 四十六、学习主线

你整理笔记时按这条主线走：

```text
 1. Tensor 是什么
 2. 创建 Tensor（tensor、zeros、ones、randn、arange、linspace）
 3. Tensor 属性（shape、dtype、device、ndim）
 4. Tensor 基本运算（+、-、*、/、**）
 5. 索引 / 切片 / 布尔索引
 6. clone / detach().clone()   ← 引用 vs 复制
 7. reshape / view / transpose / permute / squeeze / unsqueeze
 8. 矩阵乘法和 batch 矩阵乘法（* vs @ / einsum）
 9. Tensor ↔ NumPy 互转（detach、cpu、numpy）
10. manual_seed（可复现）
11. requires_grad / backward / detach / no_grad / item
12. nn.Module（__init__ + forward）/ Sequential / register_buffer / apply / nn.init
13. nn.Linear / nn.Embedding / 激活函数（nn 和 F 两种写法）/ LayerNorm / RMSNorm
14. Loss 函数（MSELoss / CrossEntropyLoss）
15. optimizer（SGD / Adam / AdamW）
16. 训练循环（前向 → 损失 → 清零 → 反向 → 裁剪 → 更新）
17. lr_scheduler（CosineAnnealing / StepLR）
18. train / eval / no_grad
19. Dataset / DataLoader
20. 保存和加载模型（state_dict）
21. Attention：QKV / GQA repeat_kv / 分数计算（@ 和 einsum）
22. causal mask 创建（tril）和应用（masked_fill）
23. logits / CrossEntropy 在大模型中的 shape
24. generate + 温度采样
```

---

## 四十七、MiniMind 对照表

你之后看 MiniMind，可以这样对应：

| MiniMind 里的概念 | PyTorch 对应知识 | 说明 |
|------------------|-----------------|------|
| `input_ids` | 整数 Tensor | token id 序列 |
| `tok_embeddings` | `nn.Embedding` | id → 向量 |
| `hidden_states` | 三维 Tensor `(B,T,C)` | 核心数据流 |
| `q_proj / k_proj / v_proj` | `nn.Linear` | 注意力投影 |
| `view / reshape` | 改 shape | 拆分/合并维度 |
| `transpose / permute` | 调整维度 | 多头注意力的维度变换 |
| `Q @ K.transpose(-2, -1)` | batch 矩阵乘法 | 注意力分数 |
| `mask` | 布尔索引 / `masked_fill` | 因果遮罩 |
| `softmax` | `torch.softmax` | 概率归一化 |
| `o_proj` | `nn.Linear` | 注意力输出投影 |
| `SwiGLU` | Linear + SiLU + Linear | 前馈网络 |
| `RMSNorm` | `nn.LayerNorm` / 自定义 `nn.Module` | 归一化层 |
| `lm_head` | `nn.Linear(C, vocab_size)` | 输出为词表大小 |
| `logits` | Tensor `(B, T, vocab_size)` | 预测分数 |
| `loss` | `CrossEntropyLoss` | 下一个 token 预测损失 |
| `optimizer` | `AdamW` | 参数更新 |
| `generate` | 循环预测 + `torch.cat` | 逐 token 生成 |
| `causal_mask` | `register_buffer` + `torch.tril` | 下三角遮罩 |
| `repeat_kv` | `repeat` / `expand` | GQA 的 KV 复制 |
| `einsum` | `torch.einsum` | 更直观的矩阵乘 |
| `apply(init_weights)` | `model.apply()` / `nn.init` | 参数初始化 |
| `clip_grad_norm_` | 梯度裁剪 | 防梯度爆炸 |
| `temperature` | `logits / temperature` + `multinomial` | 控制生成随机性 |

---

## 四十八、现阶段优先掌握什么

你现在不是为了刷 PyTorch 所有 API，而是为了**看懂 MiniMind**。

### 必须掌握

```text
Tensor / shape / dtype / device / clone / manual_seed
reshape / transpose / permute / squeeze / unsqueeze
matmul / @ / einsum
nn.Module / Sequential / Linear / Embedding / register_buffer
F.relu / F.silu（函数式激活）
CrossEntropyLoss / optimizer / clip_grad_norm_ / lr_scheduler
训练循环（前向 → 损失 → 清零 → 反向 → 裁剪 → 更新）
```

### 很重要

```text
requires_grad / backward / detach / no_grad
DataLoader / state_dict
cat / stack / repeat / expand
softmax / argmax / tril
```

### 后面慢慢补

```text
CNN / RNN / BatchNorm
复杂 DataLoader
多 GPU / 混合精度 / 分布式训练
自定义 autograd Function
```

---

## 四十九、读 MiniMind 源码的最低前置

在回头读 MiniMind 之前，你至少需要能看懂这些代码：

```python
# 1. 创建张量 + 可复现
torch.manual_seed(42)
x = torch.randn(batch_size, seq_len, hidden_size)

# 2. Sequential 快速搭模型
model = nn.Sequential(
    nn.Linear(hidden_size, hidden_size),
    nn.ReLU(),
    nn.Linear(hidden_size, vocab_size)
)

# 3. register_buffer 保存非参数张量
self.register_buffer("causal_mask", torch.tril(torch.ones(T, T)).view(1, 1, T, T))

# 4. 多头注意力的 shape 变换 + GQA
x = x.reshape(batch_size, seq_len, num_heads, head_dim)
x = x.transpose(1, 2)
k = k.repeat_interleave(num_groups, dim=1)  # GQA：复制 K heads
v = v.repeat_interleave(num_groups, dim=1)  # GQA：复制 V heads

# 5. Attention 分数（@ 和 einsum 等价）
scores = q @ k.transpose(-2, -1)
scores = torch.einsum("bhtd,bhsd->bhts", q, k)  # 语义更清晰
weights = torch.softmax(scores, dim=-1)
out = weights @ v

# 6. 完整训练步骤
optimizer.zero_grad()
loss.backward()
torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)  # 裁剪梯度
optimizer.step()
scheduler.step()
```

这些懂了，MiniMind 的骨架就能读下去了。

---

> 前置笔记：
> - [[Python基础知识体系]] — 语法地基
> - [[Python面向对象：class]] — class 是 nn.Module 的基础
> - [[NumPy系统知识]] — Tensor 就是深度学习版 ndarray
> - [[Matplotlib系统知识]] — 画训练曲线、可视化
>
> 后续笔记：
> - [[Python实践串讲：从零搭建传感器系统]] — 动手敲一遍
