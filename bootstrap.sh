#!/bin/bash -e

# there's a lot of distros where packaged qemu eithe don't support the powernv
# or only supports an old one, so build our own
if ! [ -f qemu/build/qemu-system-ppc64 ] ; then
	ci/build-qemu-powernv.sh
fi

if ! [ -f ./qemu.conf ] ; then

	# grab a PNOR and extract vmlinux/initramfs/skiboot from it
	ci/extract-pnor.sh

	# write a config for a qemu system to run op-test against
	cat - > qemu.conf <<EOF
[op-test]
bmc_type=qemu
qemu_binary=qemu/build/qemu-system-ppc64
flash_skiboot=skiboot.lid
flash_kernel=vmlinux
flash_initramfs=initramfs
EOF

	# and setup images required for the qemu based self-tests
	cp selftests/test_configs/qemu.conf.example selftests/test_configs/qemu.conf
	ln -s ../../vmlinux selftests/test_data/vmlinux
	ln -s ../../initramfs selftests/test_data/initramfs
	ln -s ../../skiboot.lid selftests/test_data/skiboot.lid
fi

if ! [ -d op-test-venv ] ; then
	python3 -m venv op-test-venv
	source op-test-venv/bin/activate
	pip3 install -r ./requirements.txt
	pip3 install -e .
fi
