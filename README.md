# Efficient-Disk-Learned-Index（DBS 课程复现说明）

本仓库基于 SIGMOD 2024 论文 **Making In-Memory Learned Indexes Efficient on Disk** 的开源实现，用于在磁盘场景下评测 learned index。

本文档替换原始 README，重点说明我们如何在 **amzn（Amazon books）** 数据集上复现“可以与论文数值完全对照/高度一致”的指标：**Table 3（模型数量节省）**与 **Table 4（索引内存占用）**，以及对应的脚本、参数与结果文件长什么样。

> 重要说明：论文里很多吞吐（ops/s）、TPC-C（txn/s）等指标与硬件强相关，不追求绝对数值逐点一致；但**模型数量（#Models）**、**模型内存占用（MiB）**、以及按论文相同 Rp 档位（Expected #I/O pages）对齐后的对照表，通常是可以做到与论文极其接近甚至一致的。

---

## 1. 我们复现了哪些“可完全对照”的指标？

我们目前完成了 **amzn（Amazon books）** 的 lookup-only microbenchmark 复现，且可与论文表格直接对照：

### 1.1 Table 3：Models Saving（#Models w/o vs w/）

论文 Table 3 比较的是：在“期望 I/O 页数（Expected #I/O pages / Rp）相同”的条件下，
引入 **disk-based zero intervals（G3）** 后，模型数量（#Models）能减少多少。

在本代码中，我们用两组索引对照来获得 “w/o（原始）” vs “w/（应用 G3）”：

- **PGM-Index**
  - w/o：`PGM Index Page_*`（脚本里调用的 `PGM-Index-Page`）
  - w/ ：`DI-V1_*`（脚本里调用的 `DI-V1`）
- **RadixSpline**
  - w/o：`RadixSplineDisk-*`（脚本里调用的 `RadixSpline`）
  - w/ ：`RS-Disk-Oriented-*`（脚本里调用的 `RS-DISK-ORIENTED`）

输出中会出现 `#model:`、以及（对 PGM）“on disk/origin version”两种 splitting 的统计，能直接对齐论文 Table 3 的 base/improved models。

### 1.2 Table 4：Memory Usage（MiB）

论文 Table 4 比较的是：在吞吐相近的条件下，不同索引设计的 **模型内存占用（MiB）**。

在本仓库中对应的索引/实现大致为：

- `Cpr_PGM`：Compressed PGM（`CompressedPGM`）
- `Cpr_PGM_D`：压缩后的 disk-based learned index 版本（脚本里用 `DI-V3`）
- `CprLeCo_PGM_D`：LeCo + 压缩的 disk-based learned index（脚本里用 `DI-V4`）
- `Zone Map`：`BinarySearch`（zone map 思路）
- `LeCo-based Zone Map`：`LecoZonemap`

> 论文 Table 4 的 “amzn 列” 是我们的主要对照目标；其它数据集列（fb/wiki/osmc）尚待补齐运行。

---

## 2. 数据集命名与论文对应关系

论文数据集名称与本仓库文件名的对应关系如下（我们当前复现的是 amzn 这一行/列）：

| 论文名称 | 本仓库数据文件名 | 备注 |
|---|---|---|
| `amzn` | `datasets/books_200M_uint64` | Amazon books（我们当前已复现） |
| `fb` | `datasets/fb_200M_uint64` | Facebook |
| `wiki` | `datasets/wiki_ts_200M_uint64` | Wikipedia edits time-id |
| `osmc` / `osm` | `datasets/osm_cellids_200M_uint64` | OpenStreetMap cell ids |

---

## 3. 环境与构建

### 3.1 依赖

构建由 `CMakeLists.txt` 管理，核心依赖包括：

- CMake（建议 ≥ 3.16）
- C++17 编译器（gcc/clang）
- `Eigen3`
- `Snappy`
- `Boost`（`serialization`、`iostreams`）
- `GMP`（`gmp`、可选 `gmpxx`）
- `OpenMP`（Linux 下通常由编译器提供）

### 3.2 编译

```bash
bash scripts/build_benchmark.sh
```

成功后 `build/` 下会生成关键可执行文件：

- `build/LID`：lookup-only microbenchmark（我们复现 Table 3/4 的核心程序）

---

## 4. 数据集准备（amzn/books）

### 4.1 数据格式

本仓库读的数据集是 **SOSD 风格二进制格式**：

- 文件头：`uint64_t size`
- 后续：`size` 个 `uint64_t` keys（可选再带 payload，但本仓库默认从 key 文件生成 payload）

### 4.2 放置数据文件

将 Amazon/books 数据放到：

```text
datasets/books_200M_uint64
```

如果你使用本仓库提供的 `GRE_datasets/` 下载文件，建议用软链接：

```bash
ln -sf ../GRE_datasets/books datasets/books_200M_uint64
```

---

## 5. 复现 Table 3（amzn，lookup-only microbenchmark）

### 5.1 运行脚本与参数

使用我们的脚本链路：

- `RunOnSingleDisk.sh` → `scripts/disk_oriented.sh` → `./build/LID`

推荐只跑 amzn（books）一个数据集：

```bash
DATASETS="books_200M_uint64" LOOKUP_COUNT=10000000 bash RunOnSingleDisk.sh
```

默认会使用机器的在线 CPU 核心数进行并行验证（线程数由代码通过 `sysconf(_SC_NPROCESSORS_ONLN)` 获取）。
如需固定线程数（例如为了与历史单线程日志对照），可通过环境变量覆盖：

```bash
LID_THREADS=1 DATASETS="books_200M_uint64" LOOKUP_COUNT=10000000 bash RunOnSingleDisk.sh
```

脚本支持通过环境变量覆盖部分参数（便于对齐论文或缩小实验）：

- `DATASETS`：空格分隔的数据集列表（例如 `books_200M_uint64`）
- `LOOKUP_COUNT`：lookup 数量（默认 10,000,000）
- `PAYLOAD_BYTES`、`TOTAL_RANGE_LIST`、`LAMBDA_LIST`：更细粒度控制（见 `scripts/disk_oriented.sh`）

### 5.2 结果输出长什么样

运行后会生成（或追加写入）类似文件：

- `results/diskOriented/res_0929_reduced_models_8B_fetch0_books_200M_uint64.csv`

注意：这个文件名里的 `0929_reduced_models` 来自脚本内的 `date` 字段（`scripts/disk_oriented.sh`），每次运行会用 `>>` 追加输出。

文件内容并不是“纯 CSV”，而是 **stdout 日志 + 部分 CSV 行混合**，典型片段包含：

- `GetModelNum of, <IndexName>, ..., #model:,<N>, space/MiB:,<M>`
- `Evaluate index on disk:,<IndexName>, ..., avg_page:,<Rp>, ... throughput:,<ops/sec>`

对照 Table 3 时，核心看 `#model:`：

- PGM base（w/o）：`PGM Index Page_*` 的 `#model`
- PGM improved（w/）：`DI-V1_*` 的 `#model`（或同段日志里的 “after pgm splitting (on disk)”）
- RS base（w/o）：`RadixSplineDisk-*` 的 `#model`
- RS improved（w/）：`RS-Disk-Oriented-*` 的 `#model`

---

## 6. 复现 Table 4（amzn，模型内存占用）

Table 4 需要跑压缩 learned index 与 zone-map 相关对比，脚本入口在：

- `scripts/compression.sh`

该脚本默认会跑多数据集；如果你只想跑 amzn/books，推荐临时在脚本里把 `dataset=(...)` 改成只保留 `books_200M_uint64`，然后运行：

```bash
bash scripts/compression.sh ./datasets/ ./results 10000000
```

输出会写入：

- `results/compression/res_<date>_8B_fetch0_books_200M_uint64.csv`（stdout 追加）

对照 Table 4 时，关注各 index 的 `space/MiB`（或 `in-memory_size`）字段。

---

## 7. 我们目前已经复现到的范围

截至目前：

- ✅ **amzn/books** 的 **Table 3（#Models）** 与 **Table 4（MiB）** 已复现，且与论文数值高度一致/可直接对照。
- ⏳ 其它 SOSD 数据集（fb/wiki/osm）同样可以按本 README 跑 microbenchmark，但我们尚未补齐输出文件。
- ⏳ YCSB / 多线程 / TPC-C 等实验属于“硬件强相关”或需要额外 workload/harness 输入（例如 `ycsb_workloads/`），尚待补充。

---

## 8. 关于结果文件是否入库

本仓库已配置 `.gitignore`，默认不会提交：

- 数据集（`datasets/`、`GRE_datasets/` 下载的大文件）
- 运行结果与日志（`results/`）
- 本地 PDF（`*.pdf`）
- 构建产物（`build/` 及 CMake 生成文件）

这样可以保证推送到 GitHub 的是“代码与脚本”，而不是大文件与本地环境产物。
