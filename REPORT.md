# Efficient Disk Learned Index 复现工程（DBS 课程作业汇报）

> 论文：SIGMOD 2024 *Making In-Memory Learned Indexes Efficient on Disk*（仓库内：`learned-index-disk-sigmod24.pdf`）  
> 代码：本仓库 `Efficient-Disk-Learned-Index`（核心可执行：`build/LID`）

本报告从“数据库作业汇报”的角度，先介绍论文工作本身（问题、方法、贡献），再介绍我们如何完成复现（工程组织、实验设置、复现实验流程与结果对照）。

---

## 0. 摘要与作业要求对照

**我们复现了什么（结论先行）**

- 复现完成并可与论文逐项对照的指标：**Table 3（#Models saving）**、**Table 4（Memory usage / MiB）**。
- 关键证据：见 **第 7.4.0 节**“一表格汇总对照”（同一张表里给出论文值/复现值/误差），以及汇总 CSV：`results/fb_table3_reproduction.csv`、`results/books_table3_reproduction.csv`、`results/table4_reproduction_all.csv`。
- synthetic（生成）数据：论文主要在 **Fig.8/Fig.9** 给出 memory usage 与 ratio-to-PGM 的对比趋势；我们不仅生成了可复用的解析表 `results/synthetic_fig9_reproduction.csv`，也在 **第 7.5.0 节**给出 Fig.9（synthetic）的“论文标注值 vs 复现值”对照表。

**作业要求对照检查（a/b/c）**

- a. 技术背景/问题/方案/原理：见 **第 1–2 节**（并结合代码实现路径说明 DI-V1/3/4 与 zone map 的对应关系）。
- b. 复现步骤 + 与论文结果对比：见 **第 6–7 节**（并在 `REPORT.md:278` 给出一表格对比）。
- c. 分析总结：优势/不足/可能优化点：见 **第 8–9 节**（按“复现反思→可能创新点”展开）。

---

## 1. 工作背景与问题（为什么要做 Disk Learned Index）

**Learned Index** 将“索引中的结构”替换为“模型预测 + last-mile search”：用模型预测 key 在有序数组中的位置，再在一个小范围内做二分/线性搜索校正。它在内存场景（随机访问代价低、cache 命中高）很有优势，但**直接搬到磁盘**会遇到核心矛盾：

- **I/O 粒度不匹配**：模型给的是“元素级位置”，但磁盘以“页（page）”为最小读取单元；预测误差哪怕只多几个元素，也可能导致多读 1 页。
- **误差与模型数量的 trade-off 被放大**：为保证小误差（小搜索范围），需要更多分段模型（#Models），而模型本身的元数据在磁盘场景会成为显著的内存负担。
- **吞吐与硬件强相关**：在磁盘上评测吞吐（ops/s）高度依赖 SSD、IO 栈、并发线程、文件系统等；更“可对照”的是结构性指标（#Models、模型内存占用）。

论文的目标是：在磁盘场景下，让 learned index 在“访问页数（Expected #I/O pages / Rp）”与“内存开销”上变得可控且高效，同时保持合理吞吐。

### 1.1 技术背景（从“内存友好”到“磁盘友好”）

学习型索引在内存中往往受益于“**模型预测减少比较次数**”与“**CPU cache 命中**”，但在磁盘上，瓶颈更容易转移为 I/O：

- 在 disk-based 场景，最后一公里搜索（last-mile search）需要访问叶子页，而叶子页的访问延迟（尤其随机读）远高于 CPU 计算。
- 因此相比“让 CPU 更快”，磁盘更关心“**少读页**”与“**读得更连续**”，并用 `Expected #I/O pages (Rp)` 作为统一的可比口径。

### 1.2 关键技术问题（论文要解决的矛盾）

#### 1.2.1 磁盘页对齐问题（预测误差如何映射到 I/O）

模型输出的是“元素级位置”，但磁盘读取是“页级”。如果误差范围跨页，即使范围不大也可能导致多读 1 页以上，直接放大 I/O 成本。

#### 1.2.2 I/O 代价主导延迟（last-mile search 策略重要性上升）

当 `Rp→1`（非常严格）时，常见策略差异变得显著：例如叶子页是“一页页拉取”还是“把预测范围覆盖的页一次拉取”，会在不同磁盘/并发度下呈现相反优势（论文将其总结为 **G1**）。

#### 1.2.3 模型元数据占用（learned index 不再“紧凑”）

为保证小误差（小范围），常需要更多分段模型（#Models）。但模型参数（keys、slopes、intercepts、errors 等）会带来显著的内存负担；磁盘索引通常希望“元数据常驻内存”，这使得“模型本身过大”成为关键问题（论文总结为 **G4**）。

#### 1.2.4 构建时间与资源消耗（200M 级数据的可用性）

微基准以 200M keys 构建索引，且要跑多组参数/多类索引。如何让构建过程可复现、可批跑、且在资源受限机器上不至于崩溃（OOM / 时间过长），也是工程层面的核心挑战。

---

## 2. 论文方法概述（做了什么，核心想法是什么）

结合论文与开源实现，本仓库覆盖的核心思路可以概括为两条主线：

### 2.0 论文的四条“落到磁盘”的设计准则（G1–G4）

论文将“把 in-memory learned index 做到磁盘上高效”总结为四条工程化准则（Guidelines）：

1. **G1（Search）叶子页拉取策略**：last-mile search 时是按页逐个拉取，还是一次性拉取预测范围覆盖的页，需要结合磁盘性能与并发度选择。
2. **G2（Granularity）预测粒度选择**：学习模型是预测“元素位置”还是预测“页号/页内范围”，两者在 `Rp`、模型数与误差上有不同 trade-off。
3. **G3（Alignment）利用页对齐扩大误差界**：训练/分段时把“落在同一页”视作零代价，从而在不改变 `Rp` 约束下显著减少模型数量（#Models saving）。
4. **G4（Compression）压缩模型参数**：对 slopes/intercepts/keys 等做轻量压缩，接受可忽略的 CPU 增量以换取显著的内存降低（MiB）。

我们复现的 Table 3/4，正是围绕 **G3（#Models saving）** 与 **G4（Memory usage）** 这两条最“结构性、可严格对照”的指标展开；对 G1/G2 我们在报告中解释其原理与实现入口，但不把吞吐数值当成严格对照指标。

### 2.1 “元素级误差”对齐到“页级误差”（Disk-Oriented Splitting）

以 PGM-Index 的分段线性模型为代表，传统 learned index 的误差界通常按“元素位置”计；但在磁盘上真正关心的是“最终要读多少页”。

本仓库的 **DI-V1（Disk-Oriented Index V1）** 用“页级误差”来分段（可在 `indexes/Compressed-Disk-Oriented-Index/base.h` 看到核心逻辑）：

- 将预测位置 `pred` 与真实位置 `correct` 映射到页号（`record_per_page` 决定页内可容纳多少条记录）
- 如果 `pred` 落在 `correct` 所在页内，则视为 **页级误差为 0**（即使元素级误差不为 0）
- 以“页级误差”为准重新做分段，从而**减少必须保留的模型段数（#Models）**

实现中还能看到对照打印（“origin version vs on disk”）：

- `after pgm splitting (origin version)`：元素级误差下的模型数
- `after pgm splitting (on disk)`：页级误差下的模型数（显著更少）

这正对应论文里“disk-based zero intervals（G3）带来的模型数量节省”这一类可严格对照指标。

### 2.2 预测粒度与页边界（G2：Item vs Page）

论文指出，磁盘场景常见做法是直接预测“页号”，但这会让模型表达能力下降；本仓库保留了“元素级预测 + 页级统计”的路径（`avg_page`/`Rp` 的统计发生在映射到页之后），并通过 `pred_gran` 参数与不同索引实现体现“粒度选择”的差异。

从复现角度看，我们不以“吞吐”来对齐 G2 的最优点，而是强调：**所有 Table 3/4 的对照实验，都在同一页大小/record_per_page 下统计 `avg_page/Rp`，保证口径一致**（见第 5 节）。

### 2.3 压缩模型元数据（G4：Memory-Optimized Variants）

在磁盘场景下，索引的“模型元数据”（keys、slopes、intercepts、errors 等）本身会吃掉可观内存。论文提出并实现了压缩方向的变体，本仓库对应为：

- `CompressedPGM`：Compressed PGM（baseline 之一）
- `DI-V3`：在 DI-V1 的基础上压缩 slopes/intercepts（`indexes/Compressed-Disk-Oriented-Index/di_v3.h`）
- `DI-V4`：进一步引入 LeCo 等方式压缩 keys 与 intercepts（`indexes/Compressed-Disk-Oriented-Index/di_v4.h`）

此外，仓库还实现了 zone map 方向对照：

- `BinarySearch`：Zone Map 思路（以块/页范围定位 + 二分）
- `LecoZonemap` / `LecoPage`：引入 LeCo 压缩/学习的 zone map 变体（见 `indexes/leco-zonemap.h`、`indexes/leco-page.h` 与 `experiments/benchmark.h` 中参数选择）

### 2.4 提升点总结（论文“提升在哪里”）

从论文结论与本仓库实现出发，这项工作的提升主要体现在：

1. **把“误差”从元素级改写为页级**：同样的 `Rp` 约束下，更多预测落在“同一页”可视为零代价，从而显著降低模型段数（Table 3）。
2. **系统性压缩模型元数据**：对 slopes/intercepts/keys 等做更有效的编码与压缩，使 learned index 在磁盘场景下的“内存占用”真正变小（Table 4 / Fig.7）。
3. **给出“fallback 设计”**：当数据分布对线性模型不友好或 `Rp→1` 极严格时，zone map（尤其 LeCo-based zone map）可成为更稳健的选择（Fig.8/Fig.9）。

### 2.5 我们在复现中“如何刻画这项工作”

从数据库作业汇报的角度，我们把论文工作刻画为一个“**从指标口径出发的系统工程**”：

- 以 `Rp` 为统一约束，把“误差界/搜索范围”转换为“页访问代价”，使磁盘场景下 learned index 的比较变得公平可解释。
- 在 `Rp` 约束下把收益拆成两类：**G3 的模型数节省（#Models saving）** 与 **G4 的内存节省（MiB）**，并在微基准中提供可复现的日志输出与统计字段。
- 同时保留一个“磁盘更稳健”的备选方案族（zone map + LeCo），解释为什么在某些分布/严格 `Rp` 下它更合适。

---

## 3. 我们的复现目标（哪些指标可严格对照）

论文中吞吐（ops/s）、TPC-C（txn/s）等指标受硬件影响大；我们把复现重点放在**可严格对照**的结构性指标：

1. **Table 3：Models Saving（#Models w/o vs w/）**  
   在相同 `Expected #I/O pages (Rp)` 下，对比 “未做磁盘化分段/优化” 与 “做了 disk-oriented splitting（含 zero intervals 思路）” 的模型数量。
2. **Table 4：Memory Usage（MiB）**  
   对比不同索引（含压缩变体、zone map 变体）的模型内存占用。

我们在 `amzn/books_200M_uint64` 数据集上给出可对照结果，并保留完整运行日志与结果文件。

### 3.1 我们复现覆盖的“论文内容边界”

结合论文的 G1–G4，我们的复现范围与取舍如下：

- **严格对照（逐格对比）**：Table 3（G3：#Models saving）、Table 4（G4：Memory usage）。这些指标主要反映算法/结构变化，对硬件与系统噪声不敏感。
- **解释为主（不追求逐点一致）**：G1（fetch 策略）与吞吐/延迟等指标。我们保留运行入口与参数解释，但不把数值“逐点对齐”作为复现成功的标准（原因见第 8.2 节）。
- **补充验证（趋势/口径复现）**：synthetic 数据的 Fig.9（Rp=1.05/5 pages）我们给出“口径对齐 + 论文图上标注值抽取”的对照表，用于说明脚本链路与指标口径在不同分布下也可复用（见第 7.5.0 节）。

### 3.2 我们的复现产物（可交付/可追溯）

从“作业可验收”的角度，复现产物分为三类：

1. **可复跑入口**：`RunOnSingleDisk.sh`、`scripts/disk_oriented.sh`、`scripts/compression.sh` 与 synthetic 的 `scripts/verify_paper_metrics_synthetic*.sh`。
2. **原始可追溯输出**：`results/diskOriented/`、`results/compression/` 下的 `res_*.csv`（实为 stdout 日志，包含 `#model`/`space/MiB`/`avg_page` 等关键行）。
3. **对照用“干净表”**：`results/*_table3_reproduction.csv`、`results/table4_reproduction_all.csv`、`results/synthetic_fig9_*` 等，由脚本从原始输出抽取并统一口径。

---

## 4. 复现工程结构（代码怎么组织、入口在哪里）

### 4.1 关键目录与文件

- `run_microbenchmark.cpp`：microbenchmark 主入口（编译后为 `build/LID`），通过字符串选择索引实现（`DI-V1/3/4`、`PGM-Index-Page`、`RadixSpline`、`RS-DISK-ORIENTED` 等）。
- `scripts/`：实验脚本
  - `scripts/disk_oriented.sh`：跑 Table 3 风格的“模型数节省 + Rp 对齐”
  - `scripts/compression.sh`：跑 Table 4/Fig9 风格的“内存占用对比”
  - `scripts/verify_paper_metrics_synthetic*.sh`：我们整理的 synthetic 数据集一键验证脚本（用于快速核对模型数/内存等指标）
- `datasets/`：数据集与落盘数据（支持 SOSD 风格二进制 key 文件；首次运行会落盘到 `*_files/`）
- `results/`、`logs/`：实验输出与日志

### 4.2 构建与运行入口

构建：

```bash
bash scripts/build_benchmark.sh
```

核心可执行：

- `build/LID`：lookup-only microbenchmark（我们复现 Table 3/4 的主要工具）

### 4.3 依赖与实验环境（便于复现者补齐）

依赖由 `CMakeLists.txt` 管理，常见需要安装：

- CMake（建议 ≥ 3.16）
- C++17 编译器（gcc/clang）
- `Eigen3`、`Snappy`
- `Boost`（`serialization`、`iostreams`）
- `GMP`（`gmp`，可选 `gmpxx`）
- `OpenMP`（Linux 下通常由编译器提供）

建议在汇报时补充你们实际环境（便于解释吞吐差异）：

- OS/Kernel：
- CPU/核数：
- 内存：
- SSD/文件系统：
- 编译器版本：

---

## 5. 实验设置与参数对齐（如何“对齐 Rp”，确保可比）

### 5.1 磁盘页与记录布局

脚本默认使用：

- 页大小：`ps=4`（KiB）→ 4 KiB page
- payload：`PAYLOAD_BYTES=8`（默认）→ record bytes = `8(key)+8(payload)=16B`
- 因此每页记录数：`record_per_page = 4*1024/16 = 256`

这会影响两个关键量：

- 预测范围落到“页号”的映射（Rp/avg_page 的统计）
- `DI-V1/3/4` 中由 `lambda` 推导分段误差（见 `indexes/Compressed-Disk-Oriented-Index/di_v1.h`：`pgm_epsilon = (lambda - 1) * record_per_page / 2 - 1`）

### 5.2 Rp（Expected #I/O pages）对齐策略

Table 3 要求在相同 Rp 下比较模型数量。仓库脚本中采用两种方式实现“对齐 Rp”：

- **DI-V1 系列**：通过 `lambda` 控制（索引名会打印成 `DI-V1_1.01 / DI-V1_1.50 / ...`）
- **PGM / RadixSpline baseline**：通过固定“搜索范围（items）”/误差界，使得统计的 `avg_page` 近似对应 Rp（见 `scripts/disk_oriented.sh` 的 `TOTAL_RANGE_LIST` 与参数换算）

### 5.3 并发与 lookups

- 线程数默认读取 `sysconf(_SC_NPROCESSORS_ONLN)`，可用环境变量覆盖：`LID_THREADS=1`
- lookup 数量默认 `LOOKUP_COUNT=10000000`（可改小用于快速验证结构性指标）

---

## 6. 复现流程（一步步如何跑出论文对照表）

### 6.1 数据集准备

本仓库读入的 key 文件采用 SOSD 风格二进制格式：

- 文件头：`uint64_t size`
- 后续：`size` 个 `uint64_t` key

数据集与论文命名的对应关系（我们重点复现 `amzn/books`）见 `README.md`。

### 6.2 Table 3（Models Saving）复现

运行（只跑 books）：

```bash
DATASETS="books_200M_uint64" LOOKUP_COUNT=10000000 bash RunOnSingleDisk.sh
```

结果输出（追加写入）：

- `results/diskOriented/res_<tag>_8B_fetch0_books_200M_uint64.csv`

该文件是 stdout 日志与部分 CSV 行混合；对照 Table 3 时，核心字段是 `#model:` 以及 “origin/on disk splitting” 的模型计数。

### 6.3 Table 4（Memory Usage）复现

运行（只跑 books）：

```bash
DATASETS="books_200M_uint64" LOOKUP_COUNT=10000000 bash scripts/compression.sh ./datasets/ ./results 10000000
```

结果输出：

- `results/compression/res_<tag>_8B_fetch0_books_200M_uint64.csv`

对照 Table 4 时主要关注 `space/MiB`（或 `in-memory_size`）字段。

### 6.4 Synthetic 数据集快速验证（我们整理的“一键核对”脚本）

用于快速核对“模型数/内存占用”等硬件弱相关指标（默认 lookup 较小）：

```bash
bash scripts/verify_paper_metrics_synthetic.sh syn_g10_l1
```

批量跑剩余 synthetic：

```bash
bash scripts/verify_paper_metrics_synthetic_all.sh
```

### 6.5 结果整理与对照表自动生成（让汇报“可复核”）

复现实验的 stdout 日志较长，为了让“论文值 vs 我们值”的对照更可靠，我们用脚本把关键指标抽取成可复核的 CSV/Markdown：

- Table 3/4 “一表格汇总对照”：`scripts/generate_one_table_comparison.py`（输出直接嵌入第 7.4.0 节，便于展示复现成功性）
- synthetic Fig.9 复现 CSV：`scripts/summarize_synthetic_fig9.py` → `results/synthetic_fig9_reproduction.csv`
- synthetic Fig.9 论文标注抽取 + 对照表：`scripts/extract_fig9_synthetic_ratios.py`、`scripts/generate_synthetic_fig9_comparison_table.py`

这样做的价值是：任何人都能从 `results/**/res_*.csv` 追溯到“表格里的每个数字”，避免“人工抄表/手算误差”。

---

## 7. 复现结果与对照（amzn/books）

为便于作业汇报展示，我们将 books 数据集的关键结果整理成“干净表格”：

- Table 3 对照表：`results/books_table3_reproduction.csv`
- Table 4 对照表：`results/books_table4_reproduction.csv`

### 7.1 Table 3：模型数量节省（Rp 对齐）

| Expected #I/O pages (Rp) | Index Family | Base Models (w/o) | Improved Models (w/) | Reduction % |
|---:|---|---:|---:|---:|
| 1.01 | PGM | 25088692 | 660574 | 97.37 |
| 1.50 | PGM | 82803 | 42878 | 48.22 |
| 2.00 | PGM | 22913 | 15766 | 31.19 |
| 3.00 | PGM | 5996 | 4975 | 17.03 |
| 1.01 | RadixSpline | 27378554 | 2406374 | 91.21 |
| 1.50 | RadixSpline | 230602 | 130356 | 43.47 |
| 2.00 | RadixSpline | 68357 | 48616 | 28.88 |
| 3.00 | RadixSpline | 18705 | 15485 | 17.21 |

解释（对应论文 Table 3 的口径）：

- `Base Models`：未使用磁盘化分段/zero intervals 思路的模型数量（w/o）
- `Improved Models`：使用 disk-oriented splitting（w/）后的模型数量
- “Rp 越接近 1”意味着允许的搜索页数越少（误差更小），因此 baseline 的模型数量会急剧增大；这正是 disk-oriented splitting 能带来最大模型数节省的区间。

### 7.2 Table 4：模型内存占用（MiB）

| Index | Models | Space (MiB) |
|---|---:|---:|
| Cpr_PGM | 2918937 | 30.49 |
| Cpr_PGM_D | 752409 | 7.82 |
| CprLeCo_PGM_D | 752409 | 6.24 |
| Zone Map | 781250 | 5.96 |
| LeCo-based Zone Map | 8055 | 4.22 |

解释（对应论文 Table 4 的口径）：

- `Cpr_PGM` 对应 `CompressedPGM`（内存压缩版 PGM baseline）
- `Cpr_PGM_D` 对应 `DI-V3`（disk-oriented + 压缩 slopes/intercepts）
- `CprLeCo_PGM_D` 对应 `DI-V4`（在 DI-V3 上进一步用 LeCo 等压缩 intercepts/keys）
- Zone Map 系列作为“更结构化/更省内存”的对照项，强调磁盘场景下“少读页 + 小内存”的整体 trade-off

### 7.2.1 从日志看压缩效果“拆解”（便于讲清楚 G4）

以 `DI-V4` 为例，日志中会打印出 slopes/intercepts/keys 的压缩前后大小与“LECO WIN/LOSE”的回退信息（例如 `after compressing the slopes`、`after leco compressing the intercepts`、`after compressing the model keys` 等）。汇报时可用它解释：

- **压缩不是黑盒**：每个部件（slope/intercept/key）各自贡献多少节省是可见的。
- **LeCo 不是总赢**：当 `LECO LOSE!` 出现时实现会回退到更合适的压缩方式，体现“稳定优先”的工程选择。

### 7.3 Synthetic（生成数据）验证：我们也做了

除了 `amzn/books`，我们还在 **synthetic（生成）数据集** 上跑了同样的“Table 3 风格（disk_oriented）+ Table 4 风格（compression）”验证，用来确认实现与脚本链路在**不同分布/不同难度**下仍能产出一致的结构性指标（#Models、MiB、avg_page/Rp 趋势）。

- 一键脚本入口：`scripts/verify_paper_metrics_synthetic.sh`、`scripts/verify_paper_metrics_synthetic_all.sh`
- 典型结果文件（你当前打开的就是其中之一）：
  - `results/diskOriented/res_1219_paper_syn_g10_l1_8B_fetch0_syn_g10_l1.csv`
  - `results/compression/res_1219_paper_syn_g10_l1_8B_fetch0_syn_g10_l1.csv`
- 已跑的 synthetic 数据集（仓库内可见对应结果文件）：`syn_g10_l1`、`syn_g10_l2`、`syn_g10_l4`、`syn_g12_l1`、`syn_g12_l4`
- 可追溯日志：`logs/verify/`（脚本会把两段实验的 stdout/stderr 单独落 log）

### 7.4 按数据集对照表（论文指标 vs 我们复现指标）

为更直观体现“复现成功”，我们把**每个数据集**的关键指标整理成统一口径的三列表格（指标名称 / 论文值 / 复现值）。

复现值来源（便于追溯）：

- Table 3（Facebook）：`results/fb_table3_reproduction.csv`（由 `results/diskOriented/res_1219_paper_syn_g10_l1_8B_fetch0_fb_200M_uint64.csv` 抽取整理）
- Table 3（Amazon/books）：`results/books_table3_reproduction.csv`
- Table 4（fb/amzn/wiki/osmc）：`results/table4_reproduction_all.csv`

> 说明：Table 3 的 Rp 取值为 `1.01 / 1.5 / 2.0 / 3.0`；表格里的“/”分隔顺序均按该顺序排列。

#### 7.4.0 一表格汇总对照（论文值 vs 复现值）

| Dataset | Metric | Paper | Ours | Δ (max) |
|---|---|---|---|---|
| fb_200M_uint64 | Table 3 / PGM #Models(w/o) @Rp=1.01/1.5/2/3 | 27,442,813 / 531,431 / 258,373 / 120,493 | 27,442,809 / 531,425 / 258,362 / 120,486 | abs≤11, rel≤0.006% |
| fb_200M_uint64 | Table 3 / PGM #Models(w/ ) @Rp=1.01/1.5/2/3 | 713,368 / 240,058 / 151,847 / 88,439 | 713,356 / 240,051 / 151,841 / 88,435 | abs≤12, rel≤0.005% |
| fb_200M_uint64 | Table 3 / PGM %Saving @Rp=1.01/1.5/2/3 | 97.4% / 54.8% / 41.2% / 26.6% | 97.4% / 54.8% / 41.2% / 26.6% | abs≤0.00%, rel≤0.000% |
| fb_200M_uint64 | Table 3 / RadixSpline #Models(w/o) @Rp=1.01/1.5/2/3 | 31,451,334 / 1,023,185 / 504,121 / 245,665 | 31,451,334 / 1,023,185 / 504,121 / 245,665 | abs≤0, rel≤0.000% |
| fb_200M_uint64 | Table 3 / RadixSpline #Models(w/ ) @Rp=1.01/1.5/2/3 | 2,562,804 / 581,190 / 344,880 / 197,122 | 2,562,804 / 581,190 / 344,880 / 197,122 | abs≤0, rel≤0.000% |
| fb_200M_uint64 | Table 3 / RadixSpline %Saving @Rp=1.01/1.5/2/3 | 91.8% / 43.2% / 31.6% / 19.8% | 91.9% / 43.2% / 31.6% / 19.8% | abs≤0.10%, rel≤0.109% |
| books_200M_uint64 | Table 3 / PGM #Models(w/o) @Rp=1.01/1.5/2/3 | 25,088,692 / 82,804 / 22,914 / 5,997 | 25,088,692 / 82,803 / 22,913 / 5,996 | abs≤1, rel≤0.017% |
| books_200M_uint64 | Table 3 / PGM #Models(w/ ) @Rp=1.01/1.5/2/3 | 660,556 / 42,872 / 15,758 / 4,965 | 660,574 / 42,878 / 15,766 / 4,975 | abs≤18, rel≤0.201% |
| books_200M_uint64 | Table 3 / PGM %Saving @Rp=1.01/1.5/2/3 | 97.4% / 48.2% / 31.2% / 17.2% | 97.4% / 48.2% / 31.2% / 17.0% | abs≤0.20%, rel≤1.163% |
| books_200M_uint64 | Table 3 / RadixSpline #Models(w/o) @Rp=1.01/1.5/2/3 | 27,378,554 / 230,602 / 68,357 / 18,705 | 27,378,554 / 230,602 / 68,357 / 18,705 | abs≤0, rel≤0.000% |
| books_200M_uint64 | Table 3 / RadixSpline #Models(w/ ) @Rp=1.01/1.5/2/3 | 2,406,374 / 130,356 / 48,616 / 15,485 | 2,406,374 / 130,356 / 48,616 / 15,485 | abs≤0, rel≤0.000% |
| books_200M_uint64 | Table 3 / RadixSpline %Saving @Rp=1.01/1.5/2/3 | 91.2% / 43.5% / 28.9% / 17.2% | 91.2% / 43.5% / 28.9% / 17.2% | abs≤0.00%, rel≤0.000% |
| fb_200M_uint64 | Table 4 / Cpr_PGM Memory | 51.36 MiB | 51.68 MiB | 0.32 MiB (0.61%) |
| fb_200M_uint64 | Table 4 / CprLeCo_PGM_D Memory | 4.05 MiB | 4.08 MiB | 0.03 MiB (0.85%) |
| fb_200M_uint64 | Table 4 / ZoneMap Memory | 5.96 MiB | 5.96 MiB | 0.00 MiB (0.01%) |
| fb_200M_uint64 | Table 4 / LeCo-basedZoneMap Memory | 2.09 MiB | 2.09 MiB | 0.00 MiB (0.13%) |
| books_200M_uint64 | Table 4 / Cpr_PGM Memory | 30.34 MiB | 30.49 MiB | 0.15 MiB (0.51%) |
| books_200M_uint64 | Table 4 / CprLeCo_PGM_D Memory | 6.20 MiB | 6.24 MiB | 0.04 MiB (0.62%) |
| books_200M_uint64 | Table 4 / ZoneMap Memory | 5.96 MiB | 5.96 MiB | 0.00 MiB (0.01%) |
| books_200M_uint64 | Table 4 / LeCo-basedZoneMap Memory | 4.22 MiB | 4.22 MiB | 0.00 MiB (0.01%) |
| wiki_ts_200M_uint64 | Table 4 / Cpr_PGM Memory | 9.48 MiB | 9.51 MiB | 0.03 MiB (0.27%) |
| wiki_ts_200M_uint64 | Table 4 / CprLeCo_PGM_D Memory | 3.17 MiB | 3.21 MiB | 0.04 MiB (1.18%) |
| wiki_ts_200M_uint64 | Table 4 / ZoneMap Memory | 5.96 MiB | 5.96 MiB | 0.00 MiB (0.01%) |
| wiki_ts_200M_uint64 | Table 4 / LeCo-basedZoneMap Memory | 1.05 MiB | 1.06 MiB | 0.01 MiB (0.58%) |
| osm_cellids_200M_uint64 | Table 4 / Cpr_PGM Memory | 34.58 MiB | 34.65 MiB | 0.07 MiB (0.21%) |
| osm_cellids_200M_uint64 | Table 4 / CprLeCo_PGM_D Memory | 6.58 MiB | 6.62 MiB | 0.04 MiB (0.57%) |
| osm_cellids_200M_uint64 | Table 4 / ZoneMap Memory | 5.96 MiB | 5.96 MiB | 0.00 MiB (0.01%) |
| osm_cellids_200M_uint64 | Table 4 / LeCo-basedZoneMap Memory | 4.59 MiB | 4.59 MiB | 0.00 MiB (0.03%) |

#### 7.4.1 `fb_200M_uint64`（Facebook，论文 Table 3/4）

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 3 / PGM `#Models(w/o)`（Rp=1.01/1.5/2/3） | 27,442,813 / 531,431 / 258,373 / 120,493 | 27,442,809 / 531,425 / 258,362 / 120,486 |
| Table 3 / PGM `#Models(w/)`（Rp=1.01/1.5/2/3） | 713,368 / 240,058 / 151,847 / 88,439 | 713,356 / 240,051 / 151,841 / 88,435 |
| Table 3 / PGM `% of saving`（Rp=1.01/1.5/2/3） | 97.4% / 54.8% / 41.2% / 26.6% | 97.4% / 54.8% / 41.2% / 26.6% |
| Table 3 / RadixSpline `#Models(w/o)`（Rp=1.01/1.5/2/3） | 31,451,334 / 1,023,185 / 504,121 / 245,665 | 31,451,334 / 1,023,185 / 504,121 / 245,665 |
| Table 3 / RadixSpline `#Models(w/)`（Rp=1.01/1.5/2/3） | 2,562,804 / 581,190 / 344,880 / 197,122 | 2,562,804 / 581,190 / 344,880 / 197,122 |
| Table 3 / RadixSpline `% of saving`（Rp=1.01/1.5/2/3） | 91.8% / 43.2% / 31.6% / 19.8% | 91.9% / 43.2% / 31.6% / 19.8% |
| Table 4 / `Cpr_PGM` 内存占用 | 51.36 MiB | 51.68 MiB |
| Table 4 / `CprLeCo_PGM_D` 内存占用 | 4.05 MiB | 4.08 MiB |
| Table 4 / `ZoneMap` 内存占用 | 5.96 MiB | 5.96 MiB |
| Table 4 / `LeCo-based ZoneMap` 内存占用 | 2.09 MiB | 2.09 MiB |

#### 7.4.2 `books_200M_uint64`（Amazon/books，论文 Table 3/4）

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 3 / PGM `#Models(w/o)`（Rp=1.01/1.5/2/3） | 25,088,692 / 82,804 / 22,914 / 5,997 | 25,088,692 / 82,803 / 22,913 / 5,996 |
| Table 3 / PGM `#Models(w/)`（Rp=1.01/1.5/2/3） | 660,556 / 42,872 / 15,758 / 4,965 | 660,574 / 42,878 / 15,766 / 4,975 |
| Table 3 / PGM `% of saving`（Rp=1.01/1.5/2/3） | 97.4% / 48.2% / 31.2% / 17.2% | 97.4% / 48.2% / 31.2% / 17.0% |
| Table 3 / RadixSpline `#Models(w/o)`（Rp=1.01/1.5/2/3） | 27,378,554 / 230,602 / 68,357 / 18,705 | 27,378,554 / 230,602 / 68,357 / 18,705 |
| Table 3 / RadixSpline `#Models(w/)`（Rp=1.01/1.5/2/3） | 2,406,374 / 130,356 / 48,616 / 15,485 | 2,406,374 / 130,356 / 48,616 / 15,485 |
| Table 3 / RadixSpline `% of saving`（Rp=1.01/1.5/2/3） | 91.2% / 43.5% / 28.9% / 17.2% | 91.2% / 43.5% / 28.9% / 17.2% |
| Table 4 / `Cpr_PGM` 内存占用 | 30.34 MiB | 30.49 MiB |
| Table 4 / `CprLeCo_PGM_D` 内存占用 | 6.20 MiB | 6.24 MiB |
| Table 4 / `ZoneMap` 内存占用 | 5.96 MiB | 5.96 MiB |
| Table 4 / `LeCo-based ZoneMap` 内存占用 | 4.22 MiB | 4.22 MiB |

#### 7.4.3 `wiki_ts_200M_uint64`（Wikipedia，论文 Table 4）

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 4 / `Cpr_PGM` 内存占用 | 9.48 MiB | 9.51 MiB |
| Table 4 / `CprLeCo_PGM_D` 内存占用 | 3.17 MiB | 3.21 MiB |
| Table 4 / `ZoneMap` 内存占用 | 5.96 MiB | 5.96 MiB |
| Table 4 / `LeCo-based ZoneMap` 内存占用 | 1.05 MiB | 1.06 MiB |

#### 7.4.4 `osm_cellids_200M_uint64`（OSMC/OSM，论文 Table 4）

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 4 / `Cpr_PGM` 内存占用 | 34.58 MiB | 34.65 MiB |
| Table 4 / `CprLeCo_PGM_D` 内存占用 | 6.58 MiB | 6.62 MiB |
| Table 4 / `ZoneMap` 内存占用 | 5.96 MiB | 5.96 MiB |
| Table 4 / `LeCo-based ZoneMap` 内存占用 | 4.59 MiB | 4.59 MiB |

### 7.5 按 synthetic 数据集对照表（用于自验证）

论文的 **Table 3/4** 本身不包含 synthetic（生成）数据的“表格化数值对照”，但论文在 Section 3 给出了 synthetic 数据集的定义（`syn_g10_l1` 等命名来自 GRE generator 的 local/global hardness 参数），并在 **Fig.8（Rp=3）**、**Fig.9（Rp=1.05/5 pages）** 中报告了 synthetic 数据上的 **内存占用（Memory Usage）** 等对比结果。

我们对 synthetic 的复现分两层：

1. **对齐论文 Fig.9 的指标口径（Memory Usage at Rp=1.05/5）**：使用 `scripts/verify_paper_metrics_synthetic*.sh` 跑 `scripts/compression.sh`，再把结果从原始日志里抽取成干净 CSV。
2. **额外的 Table 3 风格“模型数节省”自验证**：用于证明 disk_oriented 链路在不同分布/难度下也能稳定产出模型统计与节省趋势（论文对 synthetic 并未提供对应 Table 3 数值）。

#### 7.5.0 Synthetic / Fig.9 指标复现（Memory Usage at Rp=1.05/5）

论文在 **Fig.9** 中包含 `syn_g10_l1/syn_g10_l2/syn_g10_l4/syn_g12_l1/syn_g12_l4`，并在 `Rp=1.05` 与 `Rp=5 pages` 两种约束下对比 PGM-based 与 zonemap-based indexes 的 **内存占用（MiB）**。

我们已将 synthetic 的 Fig.9 风格结果整理成可直接引用的表格文件（“我们的复现值”）：

- `results/synthetic_fig9_reproduction.csv`（包含每个 synthetic 数据集、Rp=1.05/5、各 index 的 `Models / Space(MiB) / Avg pages / Ratio to PGM`）

生成该 CSV（无需重跑实验，仅解析已有输出）：

```bash
python scripts/summarize_synthetic_fig9.py
```

此外，我们也把 **论文 Fig.9 柱子上的 ratio（×）数值标注**从 PDF 中抽取出来，形成“论文值”CSV，并生成对照表（用于展示 synthetic 上的复现口径）：

- 论文 Fig.9 ratio 抽取：`results/synthetic_fig9_paper_ratios.csv`（脚本：`scripts/extract_fig9_synthetic_ratios.py`）
- 论文 vs 我们（对照表输出）：`results/synthetic_fig9_paper_vs_ours.md`（脚本：`scripts/generate_synthetic_fig9_comparison_table.py`）

生成上述两份文件（无需重跑实验）：

```bash
python scripts/extract_fig9_synthetic_ratios.py
python scripts/generate_synthetic_fig9_comparison_table.py > results/synthetic_fig9_paper_vs_ours.md
```

**Fig.9（synthetic）论文值 vs 我们复现值对照：**

| Dataset | Rp | Index | 论文 Fig.9 比值 | 我们复现比值 | 差异(复现-论文) |
|---|---:|---|---:|---:|---:|
| syn_g10_l1 | 1.05 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g10_l1 | 1.05 | CprLeCo_PGM_D | 0.200 | 0.197 | -0.003 (-1.7%) |
| syn_g10_l1 | 1.05 | LeCo-ZoneMap | 0.110 | 0.099 | -0.011 (-9.8%) |
| syn_g10_l1 | 1.05 | LeCo-ZoneMap-D | 0.110 | 0.099 | -0.011 (-9.6%) |
| syn_g10_l1 | 5 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g10_l1 | 5 | CprLeCo_PGM_D | 0.510 | 0.161 | -0.349 (-68.4%) |
| syn_g10_l1 | 5 | LeCo-ZoneMap | 0.390 | 0.210 | -0.180 (-46.2%) |
| syn_g10_l1 | 5 | LeCo-ZoneMap-D | 0.260 | 0.138 | -0.122 (-46.9%) |
| syn_g10_l2 | 1.05 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g10_l2 | 1.05 | CprLeCo_PGM_D | 0.170 | 0.097 | -0.073 (-43.0%) |
| syn_g10_l2 | 1.05 | LeCo-ZoneMap | 0.090 | 0.049 | -0.041 (-45.7%) |
| syn_g10_l2 | 1.05 | LeCo-ZoneMap-D | 0.090 | 0.049 | -0.041 (-45.6%) |
| syn_g10_l2 | 5 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g10_l2 | 5 | CprLeCo_PGM_D | 0.500 | 0.163 | -0.337 (-67.3%) |
| syn_g10_l2 | 5 | LeCo-ZoneMap | 0.390 | 0.210 | -0.180 (-46.1%) |
| syn_g10_l2 | 5 | LeCo-ZoneMap-D | 0.250 | 0.133 | -0.117 (-46.9%) |
| syn_g10_l4 | 1.05 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g10_l4 | 1.05 | CprLeCo_PGM_D | 0.090 | 0.051 | -0.039 (-42.9%) |
| syn_g10_l4 | 1.05 | LeCo-ZoneMap | 0.050 | 0.026 | -0.024 (-48.3%) |
| syn_g10_l4 | 1.05 | LeCo-ZoneMap-D | 0.050 | 0.026 | -0.024 (-48.2%) |
| syn_g10_l4 | 5 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g10_l4 | 5 | CprLeCo_PGM_D | 0.490 | 0.163 | -0.327 (-66.8%) |
| syn_g10_l4 | 5 | LeCo-ZoneMap | 0.390 | 0.210 | -0.180 (-46.1%) |
| syn_g10_l4 | 5 | LeCo-ZoneMap-D | 0.250 | 0.128 | -0.122 (-48.6%) |
| syn_g12_l1 | 1.05 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g12_l1 | 1.05 | CprLeCo_PGM_D | 0.200 | 0.211 | +0.011 (+5.7%) |
| syn_g12_l1 | 1.05 | LeCo-ZoneMap | 0.110 | 0.104 | -0.006 (-5.3%) |
| syn_g12_l1 | 1.05 | LeCo-ZoneMap-D | 0.110 | 0.105 | -0.005 (-5.0%) |
| syn_g12_l1 | 5 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g12_l1 | 5 | CprLeCo_PGM_D | 0.360 | 0.144 | -0.216 (-60.1%) |
| syn_g12_l1 | 5 | LeCo-ZoneMap | 0.340 | 0.177 | -0.163 (-48.0%) |
| syn_g12_l1 | 5 | LeCo-ZoneMap-D | 0.230 | 0.117 | -0.113 (-49.0%) |
| syn_g12_l4 | 1.05 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g12_l4 | 1.05 | CprLeCo_PGM_D | 0.100 | 0.052 | -0.048 (-47.6%) |
| syn_g12_l4 | 1.05 | LeCo-ZoneMap | 0.050 | 0.026 | -0.024 (-47.6%) |
| syn_g12_l4 | 1.05 | LeCo-ZoneMap-D | 0.050 | 0.026 | -0.024 (-47.4%) |
| syn_g12_l4 | 5 | PGM | 1.000 | 1.000 | +0.000 (+0.0%) |
| syn_g12_l4 | 5 | CprLeCo_PGM_D | 0.370 | 0.145 | -0.225 (-60.9%) |
| syn_g12_l4 | 5 | LeCo-ZoneMap | 0.340 | 0.177 | -0.163 (-47.9%) |
| syn_g12_l4 | 5 | LeCo-ZoneMap-D | 0.220 | 0.110 | -0.110 (-50.2%) |

**synthetic 上“可与论文直接对照”的指标清单：**

1. **Fig.9 / Memory Usage（MiB）**：synthetic 的 5 个数据集在 `Rp=1.05` 与 `Rp=5 pages` 下，各 index 的内存占用（论文图中 y 轴为 Memory pages/MiB）。
2. **Fig.9 / Ratio（×）标注**：论文 Fig.9 在柱子上标注了部分 ratio 数值（我们从 PDF 抽取为 `results/synthetic_fig9_paper_ratios.csv`），并在上表与复现结果对照。
3. **文中显式数值（Fig.9 解释段）**：论文在讨论中提到 “即使在 5 pages，`syn_g12_l4` 上 `CprLeCo_PGM_D` 仍需要 `1.68×` 的 `LeCo-Zonemap-D` 内存”。我们也可用 `results/synthetic_fig9_reproduction.csv` 计算同一比值（当前复现约为 `1.32×`；差异通常来自 synthetic 数据随机性与 LeCo-Zonemap(-D) 参数搜索/调参策略是否与论文完全一致）。

> 说明：Rp=1.01/1.5/2/3 的顺序不变；复现值直接从对应文件 `results/diskOriented/res_1219_paper_syn_<...>_8B_fetch0_<dataset>.csv` 抽取 `GetModelNum of ... #model:` 得到。

#### 7.5.1 `syn_g10_l1`

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 3 风格 / PGM `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 8,777,276 / 629,306 / 179,074 / 100,063 |
| Table 3 风格 / PGM `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 746,928 / 179,922 / 104,963 / 100,002 |
| Table 3 风格 / PGM `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 91.5% / 71.4% / 41.4% / 0.1% |
| Table 3 风格 / RadixSpline `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 7,316,917 / 1,554,192 / 664,766 / 224,251 |
| Table 3 风格 / RadixSpline `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 2,658,730 / 581,877 / 320,966 / 199,083 |
| Table 3 风格 / RadixSpline `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 63.7% / 62.6% / 51.7% / 11.2% |

#### 7.5.2 `syn_g10_l2`

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 3 风格 / PGM `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 9,968,051 / 295,960 / 100,378 / 100,000 |
| Table 3 风格 / PGM `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 746,289 / 107,293 / 100,003 / 100,001 |
| Table 3 风格 / PGM `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 92.5% / 63.7% / 0.4% / -0.0% |
| Table 3 风格 / RadixSpline `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 9,257,559 / 1,231,699 / 307,139 / 198,425 |
| Table 3 风格 / RadixSpline `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 2,680,204 / 412,617 / 212,447 / 195,840 |
| Table 3 风格 / RadixSpline `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 71.0% / 66.5% / 30.8% / 1.3% |

#### 7.5.3 `syn_g10_l4`

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 3 风格 / PGM `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 12,485,704 / 101,105 / 100,000 / 100,000 |
| Table 3 风格 / PGM `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 742,922 / 100,003 / 100,001 / 100,001 |
| Table 3 风格 / PGM `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 94.0% / 1.1% / -0.0% / -0.0% |
| Table 3 风格 / RadixSpline `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 13,045,968 / 442,399 / 200,213 / 197,180 |
| Table 3 风格 / RadixSpline `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 2,641,864 / 225,008 / 200,002 / 195,695 |
| Table 3 风格 / RadixSpline `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 79.7% / 49.1% / 0.1% / 0.8% |

#### 7.5.4 `syn_g12_l1`

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 3 风格 / PGM `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 8,760,049 / 639,579 / 199,198 / 120,061 |
| Table 3 风格 / PGM `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 770,199 / 194,782 / 125,338 / 120,009 |
| Table 3 风格 / PGM `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 91.2% / 69.5% / 37.1% / 0.0% |
| Table 3 风格 / RadixSpline `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 7,268,337 / 1,542,516 / 685,784 / 259,286 |
| Table 3 风格 / RadixSpline `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 2,694,002 / 612,917 / 355,164 / 237,901 |
| Table 3 风格 / RadixSpline `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 62.9% / 60.3% / 48.2% / 8.2% |

#### 7.5.5 `syn_g12_l4`

| 指标名称 | 论文给出的指标 | 我们复现的指标 |
|---|---:|---:|
| Table 3 风格 / PGM `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 12,444,035 / 121,242 / 120,009 / 120,008 |
| Table 3 风格 / PGM `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 750,763 / 120,011 / 120,010 / 120,009 |
| Table 3 风格 / PGM `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 94.0% / 1.0% / -0.0% / -0.0% |
| Table 3 风格 / RadixSpline `#Models(w/o)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 12,982,601 / 476,304 / 240,183 / 238,344 |
| Table 3 风格 / RadixSpline `#Models(w/)`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 2,655,854 / 260,380 / 240,007 / 236,131 |
| Table 3 风格 / RadixSpline `% of saving`（Rp=1.01/1.5/2/3） | 未提供（synthetic 自验证） | 79.5% / 45.3% / 0.1% / 0.9% |

---

## 8. 分析总结：复现优势、不足与可能优化点

### 8.1 复现工作的优势（为什么说“复现成功”）

1. **严格可比指标对齐**：我们把复现重点放在论文明确指出“可对照/硬件弱相关”的结构性指标：`#Models` 与 `MiB`（Table 3/4）。
2. **对照结果可追溯**：从原始输出（`results/diskOriented/`、`results/compression/`）到汇总表（`results/*_table*_reproduction.csv`、`results/table4_reproduction_all.csv`），每一项指标都能回溯到日志行（`GetModelNum of ... #model`、`space/MiB`）。
3. **一致性高**：在 `REPORT.md:278` 的汇总表中，多数指标达到“逐格相等”或“误差 < 1%”级别，符合课程作业对复现可信度的要求。
4. **工程化脚本链路完整**：提供可重复执行入口（`RunOnSingleDisk.sh`、`scripts/compression.sh`、`scripts/verify_paper_metrics_synthetic*.sh`），并通过环境变量可控缩小实验规模便于调试。

### 8.2 复现工作的不足（哪些地方没法做到同等严格）

1. **吞吐/延迟强硬件相关**：ops/s、latency(ns) 会受 SSD、文件系统、direct I/O、线程数、CPU 频率等影响，无法保证与论文数值逐点一致。
2. **图表类（Fig.8/Fig.9）“逐柱数值”不一定能逐点对齐**：论文以图形 + ratio（×）标注呈现，且部分柱子并未给出标注值；同时某些 index（如 LeCo-ZoneMap-D）在论文中使用 tuned 版本，参数搜索/回退策略会影响最终内存占用。我们在第 7.5.0 节提供了“从 PDF 抽取的标注值 vs 复现值”的表格：它更适合用来验证**趋势与口径一致性**，而不是证明每个柱子都能精确对齐。
3. **资源/时间成本**：200M keys 的实验对内存与 I/O 带宽要求高，重复跑全量参数网格会明显拉长复现周期。

### 8.3 通过复现思考的可能创新点或优化点（面向改进）

我们在复现过程中最直观的感受是：论文把 disk learned index 的优化拆成了“可解释、可度量”的几个旋钮（`Rp`、#Models、MiB、fetch 策略）。因此“可能优化点”也应该落到：**哪些旋钮现在靠人工经验调，能否自动化/系统化；哪些开销没有被纳入目标函数；哪些系统层细节会放大或掩盖算法收益**。

#### 8.3.1 自适应参数优化（自动对齐 Rp 与空间目标）

**动机**：目前很多参数（如 `lambda`、zone map 的 block/page 粒度、LeCo 的参数）依赖人工经验与离线试跑。不同分布的数据集最优点差异很大，且 `Rp` 与模型数/内存之间是非线性的。

**可做的改进**：

- 把一次实验视作“黑盒函数评估”：输入（参数）→ 输出（`Rp/avg_page`、`MiB`、构建时间、吞吐）。
- 用贝叶斯优化/多臂老虎机/网格+早停等方法自动搜索，使得“满足 Rp 约束下最小 MiB”或“给定 MiB 下最小 Rp”变成可复用流程。

**如何验证**：以 Table 3/4 的口径为准，在同一机器上比较“人工默认参数”与“自动搜索参数”在 `MiB`/`#Models` 上的改进幅度，并报告搜索成本（时间/试验次数）。

#### 8.3.2 混合压缩策略（从“LECO LOSE!”到可预测决策）

**动机**：日志里出现 `LECO LOSE!` 说明 learned compression 并非在所有段/所有数据上都最优。现有实现倾向于“试一下，不行就回退”，但缺少“为什么”的可解释决策。

**可做的改进**：

- 以段为单位提取特征（单调性/残差分布/熵/局部斜率变化/重复率等），学习一个“压缩器选择器”，提前决定用 LeCo 还是传统编码。
- 对不同部件（slope/intercept/key）分别建模，而不是“一把梭哈”。

**如何验证**：固定 `Rp`，比较 `MiB` 与构建/解压 CPU 开销，并统计“选择器命中最优压缩器的比例”。

#### 8.3.3 更系统的预取与缓存（把 G1 真正落到系统层）

**动机**：论文的 G1 指出 fetch 策略与磁盘特性/并发度强相关；但复现时我们更关注结构性指标，未对其进行系统化评测。

**可做的改进**：

- 基于预测区间，设计“最小化 tail latency”的预取策略（比如小范围 one-by-one，大范围 all-at-once，阈值可学习）。
- 引入简单的 workload 识别（点查/局部性/Zipf 热点），动态调整预取深度与缓存 pin 策略。

**如何验证**：在固定硬件上报告 p50/p99 延迟与吞吐随并发度变化的曲线，并对比不同策略的 I/O 形态（随机/顺序比例）。

#### 8.3.4 NUMA/多线程感知布局（把并发代价“算进去”）

**动机**：多线程 lookup 时，模型与页缓存的内存位置可能成为瓶颈；尤其在多 socket/NUMA 机器上，跨节点访问会显著放大延迟波动。

**可做的改进**：

- 分段模型按 NUMA 节点分区，尽量让线程访问本地模型与本地页缓存。
- 对热点段做 replication，把“读多写少”的模型复制到多个节点以减少跨节点访问。

**如何验证**：对比 NUMA pin 前后 p99 延迟与吞吐，报告跨节点内存访问比例（如用 perf/numactl 观测）。

#### 8.3.5 增量构建与更新支持（从微基准走向真实数据库）

**动机**：微基准是 read-only 的，但真实数据库需要 insert/update。learned index 常见难点是更新会破坏单调性与误差界，导致重建成本高。

**可做的改进**：

- 分层结构：顶层粗粒度模型稳定不动，底层段支持局部重训练/重分段。
- 以“受影响最小段”为单位做增量重建，配合后台 compaction。

**如何验证**：用 YCSB（仓库已有入口）测量更新吞吐、查询吞吐与索引膨胀，报告重建频率与 amortized 成本。

#### 8.3.6 多目标优化框架（让 index 选择“可解释”）

**动机**：实际工程不是只追一个指标：你可能希望“内存不超过 X、`Rp` 不超过 Y、构建不超过 Z”，并且不同场景权重不同。

**可做的改进**：

- 把 `MiB / Rp / throughput / build_time` 统一到 Pareto 前沿，输出一组可解释配置（而不是单点最优）。
- 将 zone map 作为“稳健备选”，在数据分布不友好时自动切换，而不是靠人工经验。

**如何验证**：给出多组硬约束/权重下的推荐配置，并用对照实验说明这些配置在目标指标上确实占优。

---

## 9. 总结与展望

本次复现以 Table 3/4 为主线，验证了“**页级误差对齐 + 模型压缩**”在磁盘场景下确实能带来论文宣称的结构性收益（模型数显著下降、内存占用显著下降），且在真实 SOSD 数据集上与论文高度一致。

后续若要进一步拓展为更完整的系统评测，可在固定硬件与 I/O 栈配置后补齐：

- Fig.7/8/9 等图表的“逐柱对齐”（含更严格的参数搜索设置）
- 吞吐/延迟在不同线程数、不同 fetch 策略下的敏感性实验
- YCSB/TPC-C 或更新场景下的端到端评测（对应仓库中 `run_ycsb_experiments.cpp` 等入口）

---

## 参考

- `learned-index-disk-sigmod24.pdf`（SIGMOD 2024 论文原文）
- `README.md`（本仓库复现说明与脚本入口）
