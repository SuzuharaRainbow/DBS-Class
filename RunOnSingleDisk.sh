
bash scripts/build_benchmark.sh

src="./datasets/"
resfile="./results"
lookup=10000000

if [ -n "${LOOKUP_COUNT}" ]; then
    lookup=${LOOKUP_COUNT}
fi

# bash scripts/PG_SameItems.sh $src $resfile
# bash scripts/PG_Space.sh $src $resfile $lookup
bash scripts/disk_oriented.sh $src $resfile $lookup
# bash scripts/fetch_strategy.sh $src $resfile $lookup
# bash scripts/test_disk.sh $src $resfile
# bash scripts/compression.sh $src $resfile $2
