
#include <fstream>
#include <string>
#include <utility>
#include <vector>

#include "./util.h"

#ifndef EXPERIMENTS_TEST_DISK_H_
#define EXPERIMENTS_TEST_DISK_H_

pthread_mutex_t mutex_task;

template <typename IndexType>
static void* TestDiskCore(void* thread_params) {
  typedef typename IndexType::K_ K;
  ThreadParams<IndexType> tmp_params =
      *static_cast<ThreadParams<IndexType>*>(thread_params);
  uint64_t data_num =
      tmp_params.params.dataset_bytes_ / tmp_params.params.record_bytes_;
  const uint64_t kGapCnt = tmp_params.params.record_bytes_ / sizeof(K);
  ResultInfo<K>* res_info = new ResultInfo<K>();
  auto size = tmp_params.lookups.size();

  res_info->latency_sum = GetNsTime([&] {
    for (uint64_t i = 0; i < size; i++) {
      SearchRange range = {tmp_params.lookups[i].second - tmp_params.diff,
                           tmp_params.lookups[i].second + 1};
      if (tmp_params.lookups[i].second < tmp_params.diff) {
        range.start = 0;
      }
      range.stop = std::min(range.stop, data_num);

      ResultInfo<K> read_res = NormalCoreLookup(
          range, tmp_params.lookups[i].first, tmp_params.params, kGapCnt);

      res_info->total_search_range += read_res.total_search_range;
      if (read_res.total_search_range > res_info->max_search_range) {
        res_info->max_search_range = read_res.total_search_range;
      }
      res_info->res += read_res.res;
      res_info->fetch_page_num += read_res.fetch_page_num;
      res_info->total_io += read_res.total_io;
      res_info->ops++;
    }
  });
  return static_cast<void*>(res_info);
}

template <typename IndexType>
void TestDisk(const typename IndexType::DataVev_& data,
              const uint64_t lookup_num,
              const Params<typename IndexType::K_>& params,
              const typename IndexType::param_t diff) {
  std::cout << "\nTest Disk: " << diff << std::endl;

  auto seed = std::chrono::system_clock::now().time_since_epoch().count();
  typename IndexType::DataVev_ tmp_lookups(lookup_num);
  typename IndexType::K_ actual_res = 0;
  for (uint64_t i = 0, cnt = 0; i < lookup_num; i++) {
    if (cnt >= data.size()) {
      cnt = 0;
    }
    tmp_lookups[i] = data[cnt];
    cnt += params.record_num_per_page_ / 4;
    actual_res += tmp_lookups[i].first;
  }
  std::shuffle(tmp_lookups.begin(), tmp_lookups.end(),
               std::default_random_engine(seed));

  ResultInfo<typename IndexType::K_> res_info;
  mutex_task = PTHREAD_MUTEX_INITIALIZER;
  const size_t thread_num =
      params.is_on_disk_ ? GetConfiguredThreadCount() : 1;
  uint64_t ns = GetNsTime([&] {
    const size_t tn = std::max<size_t>(1, thread_num);
    if (tn == 1 || lookup_num == 0) {
      ThreadParams<IndexType> tmp_params(params, IndexType(), tmp_lookups, diff);
      auto* tmp =
          static_cast<ResultInfo<typename IndexType::K_>*>(TestDiskCore<IndexType>(
              static_cast<void*>(&tmp_params)));
      res_info = *tmp;
      delete tmp;
      res_info.ops = lookup_num;
      return;
    }

    std::vector<pthread_t> thread_handles(tn);
    std::vector<ThreadParams<IndexType>> thread(tn);

    const uint64_t seg = lookup_num / tn;
    for (size_t i = 0; i < tn; i++) {
      const uint64_t begin = static_cast<uint64_t>(i) * seg;
      const uint64_t end =
          (i == tn - 1) ? lookup_num : static_cast<uint64_t>(i + 1) * seg;
      thread[i].lookups = typename IndexType::DataVev_(
          tmp_lookups.begin() + begin, tmp_lookups.begin() + end);
      thread[i].params = params;
      thread[i].diff = diff;
      pthread_create(&thread_handles[i], nullptr, TestDiskCore<IndexType>,
                     static_cast<void*>(&thread[i]));
    }

    for (size_t i = 0; i < tn; i++) {
      void* tmp_ret = nullptr;
      pthread_join(thread_handles[i], &tmp_ret);
      auto* tmp = static_cast<ResultInfo<typename IndexType::K_>*>(tmp_ret);
      res_info.total_search_range += tmp->total_search_range;
      res_info.max_search_range =
          std::max(res_info.max_search_range, tmp->max_search_range);
      res_info.res += tmp->res;
      res_info.fetch_page_num += tmp->fetch_page_num;
      res_info.total_io += tmp->total_io;
      res_info.ops += tmp->ops;
      delete tmp;
    }
  });
  pthread_mutex_destroy(&mutex_task);

  double latency = ns * 1.0 / res_info.ops;

#define FAST_CHECK
#ifdef FAST_CHECK
  std::ofstream output("results/testDisk/prof_res.csv",
                       std::ios::app | std::ios::out);
  // output << "#threads, diff, fetch strategy, ops, throughput, latency\n";
  output << thread_num << ", " << diff << ", " << params.fetch_strategy_
         << ", " << res_info.ops << ", " << res_info.ops * 1.0 / ns * 1e9
         << ", " << latency << "\n";

#endif

  std::cout << "Evaluate index on disk:,DISK_" << diff << ",,,, avg_time:,"
            << ns * 1.0 / res_info.ops << ", ns,"
            << ",,, #ops," << res_info.ops << ",, avg_page:,"
            << res_info.fetch_page_num * 1.0 / res_info.ops << ", avg_range:,"
            << res_info.total_search_range * 1.0 / res_info.ops
            << ", max_range:," << res_info.max_search_range << ", pred_gran:,"
            << params.pred_granularity_ << ", fetch_strategy_:,"
            << params.fetch_strategy_;
  if (res_info.res == actual_res || res_info.ops != lookup_num) {
    std::cout << ", FIND SUCCESS,,,";
  } else {
    std::cout << ", FIND WRONG res:," << res_info.res << ", actual res:,"
              << actual_res;
  }
  std::cout << ",,,,, #threads:," << thread_num << ", throughput:,"
            << res_info.ops * 1.0 / ns * 1e9 << ", ops/sec, avg_io:,"
            << res_info.total_io * 1.0 / res_info.ops << ", total IO:,"
            << res_info.total_io << ", IOPS:,"
            << res_info.total_io * 1.0 / ns * 1e9 << ", Bandwidth:,"
            << params.page_bytes_ / 1024.0 / 1024.0 / 1024.0 *
                   res_info.fetch_page_num / ns * 1e9
            << ", GB/s, latency:," << latency << ", ns";
  std::cout << std::endl;
}

#endif  // EXPERIMENTS_TEST_DISK_H_
