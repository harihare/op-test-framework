#!/bin/bash
set -e
set -vx

# Check if we already have a qemu binary. Nothing to be done otherwise.
if [ -f "qemu/ppc64-softmmu/qemu-system-ppc64" ] ; then
	exit 0;
fi

repo="https://github.com/qemu/qemu.git"
branch="master"

#repo="https://github.com/open-power/qemu.git"
#branch="qemu-powernv-for-skiboot-7"

# grab the SHA of the current upstream master
head_sha="$(git ls-remote --heads $repo $branch | cut -f 1)"
cachedir="ci_cache/qemu/$head_sha/"

if [ -f "$cachedir/qemu-system-ppc64" ] ; then
	mkdir -p qemu/ppc64-softmmu/
	cp $cachedir/qemu-system-ppc64 qemu/ppc64-softmmu/
	exit 0
fi

# zap any existing cached builds
rm -rf ci_build_cache/qemu/

# otherwise build from source
git clone --depth=1 -b $branch $repo

cd qemu
git submodule update --init dtc
./configure --target-list=ppc64-softmmu --disable-werror --python=/usr/bin/python3
make -j $(grep -c processor /proc/cpuinfo)

# prep the cache
cd ..
mkdir -p $cachedir
cp qemu/ppc64-softmmu/qemu-system-ppc64 $cachedir/qemu-system-ppc64
