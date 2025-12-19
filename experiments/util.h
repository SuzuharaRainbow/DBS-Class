#ifndef EXPERIMENTS_UTIL_H_
#define EXPERIMENTS_UTIL_H_

#include <algorithm>
#include <vector>

#include "util_compression.h"
#include "util_lid.h"
#include "util_same_block_size.h"

// Multithreaded lookup execution uses pthreads; thread count is runtime-configured.

/**
 * @brief Return the range in item-level: [start, stop)
 */
inline void GetItemRange(SearchRange* range, uint64_t pred_gran,
                         uint64_t data_size) {
  range->start = std::min<size_t>(range->start * pred_gran, data_size - 1);
  range->stop = std::min<size_t>(range->stop * pred_gran, data_size);
}

template <typename IndexType>
static void* DoCoreLookups(void* thread_params) {
  typedef typename IndexType::K_ K;
  ThreadParams<IndexType> tmp_params =
      *static_cast<ThreadParams<IndexType>*>(thread_params);
  uint64_t data_num =
      tmp_params.params.dataset_bytes_ / tmp_params.params.record_bytes_;
  const uint64_t kGapCnt = tmp_params.params.record_bytes_ / sizeof(K);
  ResultInfo<K>* res_info = new ResultInfo<K>;
  auto size = tmp_params.lookups.size();

  res_info->latency_sum = GetNsTime([&] {
    for (uint64_t i = 0; i < size; i++) {
      SearchRange range;
      res_info->index_predict_time += GetNsTime([&] {
        range = tmp_params.index.Lookup(tmp_params.lookups[i].first);
      });

      ResultInfo<K> read_res;
#ifdef PROF_CPU_IO
      res_info->cpu_time += GetNsTime([&] {
#endif  // PROF_CPU_IO
        GetItemRange(&range, tmp_params.params.pred_granularity_, data_num);
#ifdef PROF_CPU_IO
      });
#endif  // PROF_CPU_IO
      if (!tmp_params.params.is_compression_mode_) {
        if (tmp_params.params.pred_granularity_ > 1) {
          range.stop--;
        }
        read_res = NormalCoreLookup(range, tmp_params.lookups[i].first,
                                    tmp_params.params, kGapCnt);
      } else {
        read_res = CompressionCoreLookup(range, tmp_params.lookups[i].first,
                                         tmp_params.params, kGapCnt);
      }
      res_info->total_search_range += read_res.total_search_range;
      if (read_res.total_search_range > res_info->max_search_range) {
        res_info->max_search_range = read_res.total_search_range;
      }
      res_info->res += read_res.res;
      res_info->fetch_page_num += read_res.fetch_page_num;
      res_info->total_io += read_res.total_io;
      res_info->cpu_time += read_res.cpu_time;
      res_info->io_time += read_res.io_time;
      res_info->ops++;
    }
  });
  return static_cast<void*>(res_info);
}

template <typename IndexType>
static inline ResultInfo<typename IndexType::K_> DoLookups(
    const IndexType& index, const typename IndexType::DataVev_& lookups,
    const Params<typename IndexType::K_>& params, size_t thread_num) {
  typedef typename IndexType::K_ K;
  ResultInfo<K> res_info;
  uint64_t size = lookups.size();

  thread_num = std::max<size_t>(1, thread_num);
  if (thread_num == 1 || size == 0) {
    ThreadParams<IndexType> tmp_params(params, index, lookups,
                                       typename IndexType::param_t());
    auto* tmp =
        static_cast<ResultInfo<K>*>(DoCoreLookups<IndexType>(&tmp_params));
    res_info = *tmp;
    delete tmp;
    res_info.ops = size;
    res_info.latency_sum = size ? (res_info.latency_sum * 1.0 / size) : 0;
    return res_info;
  }

  std::vector<pthread_t> thread_handles(thread_num);
  std::vector<ThreadParams<IndexType>> thread(thread_num);

  const uint64_t seg = size / thread_num;
  for (size_t i = 0; i < thread_num; i++) {
    const uint64_t begin = static_cast<uint64_t>(i) * seg;
    const uint64_t end =
        (i == thread_num - 1) ? size : static_cast<uint64_t>(i + 1) * seg;
    thread[i].lookups =
        typename IndexType::DataVev_(lookups.begin() + begin, lookups.begin() + end);
  }

  for (size_t i = 0; i < thread_num; i++) {
    thread[i].index = index;
    thread[i].params = params;
    thread[i].params.alloc();
    thread[i].params.open_files =
        OpenFiles(params.data_dir_, static_cast<int>(params.open_files.size()));
    pthread_create(&thread_handles[i], nullptr, DoCoreLookups<IndexType>,
                   static_cast<void*>(&thread[i]));
  }

  for (size_t i = 0; i < thread_num; i++) {
    void* tmp_ret = nullptr;
    pthread_join(thread_handles[i], &tmp_ret);
    auto* tmp = static_cast<ResultInfo<K>*>(tmp_ret);
    res_info.total_search_range += tmp->total_search_range;
    res_info.max_search_range = std::max(res_info.max_search_range, tmp->max_search_range);
    res_info.res += tmp->res;
    res_info.fetch_page_num += tmp->fetch_page_num;
    res_info.total_io += tmp->total_io;
    res_info.ops += tmp->ops;
    res_info.index_predict_time += tmp->index_predict_time;
    // latency_sum from each thread is thread-local wall time; not aggregated for reporting.
    delete tmp;
  }

  for (auto& t : thread) {
    CloseFiles(t.params.open_files);
  }

  return res_info;
}

template <typename IndexType>
static inline ResultInfo<typename IndexType::K_> DoMemoryLookups(
    const IndexType& index, const typename IndexType::DataVev_& data,
    const typename IndexType::DataVev_& lookups, uint64_t pred_gran) {
  uint64_t size = lookups.size();
  typedef typename IndexType::K_ K;
  ResultInfo<K> res_info;
  for (uint64_t i = 0; i < size; i++) {
    SearchRange range;
    res_info.index_predict_time +=
        GetNsTime([&] { range = index.Lookup(lookups[i].first); });
    GetItemRange(&range, pred_gran, data.size());

    res_info.total_search_range += range.stop - range.start;
    if (range.stop - range.start > res_info.max_search_range) {
      res_info.max_search_range = range.stop - range.start;
    }

#if LAST_MILE_SEARCH == 0
    auto it = std::lower_bound(
        data.begin() + range.start, data.begin() + range.stop, lookups[i].first,
        [](const auto& lhs, const K key) { return lhs.first < key; });
#else
    auto it = data.begin() + range.start;
    while (it != data.begin() + range.stop) {
      if (it->first == lookups[i].first) {
        break;
      } else {
        it++;
      }
    }
#endif
    res_info.res += it->first;
    size_t cnt = 1;

    while (++it != data.end() && it->first == lookups[i].first &&
           cnt++ < MAX_NUM_QUALIFYING) {
      res_info.res += it->first;
    }
  }
  res_info.ops = size;
  return res_info;
}

#endif  // EXPERIMENTS_UTIL_H_
