# Efficient-Disk-Learned-Index（DBS 课程复现工程）

本仓库基于 SIGMOD 2024 论文 **Making In-Memory Learned Indexes Efficient on Disk** 的开源实现，用于在“磁盘/页 I/O”视角下评测 learned index，并配套了我们用于 DBS 课程作业的复现脚本与结果汇总。

- 完整汇报：`REPORT.md`（对应 `REPORT.docx`）
- 复现重点：尽量复现“与硬件弱相关/可严格对照”的指标（模型数量、模型内存等），而不是追求吞吐/延迟绝对值逐点一致

## 1) 我们现在复现到什么程度？

结论：**Table 3 + Table 4 的数值基本可以认为“复现成功（误差≈0）”**；其它更偏“系统/硬件”类的指标（吞吐、YCSB、TPC-C）只做了部分工程支持，不作为严格对照目标。

### 1.1 可严格对照（基本复现）

- **Table 3 / Models Saving（#Models w/o vs w/）**：`fb` + `amzn`
  - 汇总文件：`results/fb_table3_reproduction.csv`、`results/books_table3_reproduction.csv`
- **Table 4 / Memory Usage（MiB）**：`fb` + `amzn` + `wiki` + `osmc`
  - 汇总文件：`results/table4_reproduction_all.csv`
- **一键对照表（Paper vs Ours）**：可直接读我们生成的快照
  - `results/paper_table34_paper_vs_ours.md`

### 1.2 半严格对照（趋势复现，部分数值仍有偏差）

- **Fig.9（synthetic）/ Ratio-to-PGM**：我们提供了“论文标注值 vs 复现值”的对照表
  - 论文标注值（从 PDF 抽取的数值）：`results/synthetic_fig9_paper_ratios.csv`
  - 我们复现值（从日志汇总）：`results/synthetic_fig9_reproduction.csv`
  - 对照表：`results/synthetic_fig9_paper_vs_ours.md`

对 Fig.9 来说，`Rp=1.05` 组更接近论文标注值；`Rp=5` 组存在系统性偏差（偏差方向一致，主要体现在比值更小），更像是“实现/参数/统计口径”的差异而不是随机噪声，因此在 README/报告中我们把它标为“趋势复现、数值待进一步对齐”。

### 1.3 硬件强相关（不作为严格复现目标）

- 吞吐（ops/s）、IOPS、延迟（ns）等：用于验证趋势与 sanity check，但不追求与论文逐点一致
- 多线程 YCSB：仓库内有 harness（`scripts/multi_threaded/`、`build/MULTI-HYBRID-LID`），但未完成整套论文级别复现

## 2) 快速开始（推荐）

### 2.1 构建

```bash
bash scripts/build_benchmark.sh
```

成功后会生成关键可执行文件：

- `build/LID`：lookup-only microbenchmark（Table 3/4、Fig.9 汇总日志来源）
- `build/MULTI-HYBRID-LID`：多线程 YCSB harness（非严格复现目标）

### 2.2 数据集放置

论文与本仓库数据文件命名对应关系：

| 论文名称 | 本仓库路径（文件） |
|---|---|
| `amzn` | `datasets/books_200M_uint64` |
| `fb` | `datasets/fb_200M_uint64` |
| `wiki` | `datasets/wiki_ts_200M_uint64` |
| `osmc` / `osm` | `datasets/osm_cellids_200M_uint64` |

数据格式为 SOSD 风格二进制：

- 文件头：`uint64_t size`
- 后续：`size` 个 `uint64_t` keys

如果你用 `GRE_datasets/` 下载文件，建议用软链接（示例以 books 为例）：

```bash
ln -sfn ../GRE_datasets/books datasets/books_200M_uint64
```

## 3) 复现实验入口

### 3.1 Table 3（Models Saving）

入口脚本链路：

- `RunOnSingleDisk.sh` → `scripts/disk_oriented.sh` → `./build/LID`

仅跑 books（amzn）：

```bash
DATASETS="books_200M_uint64" LOOKUP_COUNT=10000000 bash RunOnSingleDisk.sh
```

仅跑 fb + books：

```bash
DATASETS="fb_200M_uint64 books_200M_uint64" LOOKUP_COUNT=10000000 bash RunOnSingleDisk.sh
```

### 3.2 Table 4（Memory Usage）

入口脚本：

- `scripts/compression.sh`

只跑 books（amzn）：

```bash
DATASETS="books_200M_uint64" LOOKUP_COUNT=10000000 bash scripts/compression.sh ./datasets/ ./results 10000000
```

> `scripts/compression.sh` 会把 stdout 追加写到 `results/compression/res_<date>_*.csv`（目录默认不入库），Table 4 的汇总来自我们从这些日志里提取出来的 `results/table4_reproduction_all.csv`。

## 4) 多核加速（我们做了什么，怎么用）

### 4.1 `build/LID` 的并行 lookup

在 on-disk 评测路径中，`build/LID` 的 lookup 执行支持用 pthread 并行拆分查询（每个线程各自打开文件句柄）。

- 默认线程数：`sysconf(_SC_NPROCESSORS_ONLN)`（即在线 CPU 核心数）
- 手动指定线程数：环境变量 `LID_THREADS`

示例（固定为单线程以便与历史日志逐行对照）：

```bash
LID_THREADS=1 DATASETS="books_200M_uint64" LOOKUP_COUNT=10000000 bash RunOnSingleDisk.sh
```

注意：并行 lookup 的加速幅度受磁盘/IO 限制很大；在单块盘上更容易出现“线程多了但吞吐不涨”的情况（甚至回退），这属于预期现象。

### 4.2 OpenMP（构建/训练阶段的并行）

部分索引构建阶段使用 OpenMP；可用 `OMP_NUM_THREADS` 控制 OpenMP 并行度（若系统/编译器未启用 OpenMP，则相关逻辑会退化为单线程）。

## 5) 结果文件与“入库策略”

为了让仓库可复现且不塞大文件，我们的约定是：

- ✅ 提交“小而关键”的汇总/对照文件：`results/*.csv`、`results/*.md`
- ⛔ 不提交大文件：数据集（`datasets/`、`GRE_datasets/`）、运行日志（`results/compression/`、`results/diskOriented/`）、构建产物（`build/`）
- ⛔ 不提交论文 PDF（`*.pdf`）：如需跑“从 PDF 自动解析 Table 3/4 数值”的脚本，请自行下载并放到仓库根目录（默认文件名 `learned-index-disk-sigmod24.pdf`）

## 6) 生成/更新对照表（可选）

如果你本地放了论文 PDF（默认 `learned-index-disk-sigmod24.pdf`），可以自动生成 “Table 3/4: Paper vs Ours” 对照表：

```bash
python3 scripts/generate_one_table_comparison.py > results/paper_table34_paper_vs_ours.md
```
