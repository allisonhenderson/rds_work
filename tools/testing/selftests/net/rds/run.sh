#! /bin/bash

set -e
set -u

current_dir="$(realpath "$(dirname "$0")")"

# This script currently only works for x86_64
ARCH="$(uname -m)"
case "${ARCH}" in
x86_64)
        QEMU_BINARY=qemu-system-x86_64
        ;;
*)
        echo "Unsupported architecture"
        exit 4
        ;;
esac

# Kselftest framework requirement - SKIP code is 4.
check_env()
{
	if ! which tcpdump > /dev/null 2>&1; then
		echo "selftests: [SKIP] Could not run test without tcpdump"
		exit 4
	fi

	if ! which gcovr > /dev/null 2>&1; then
		echo "selftests: [SKIP] Could not run test without gcovr"
		exit 4
	fi

	if ! which $QEMU_BINARY > /dev/null 2>&1; then
		echo "selftests: [SKIP] Could not run test without qemu"
		exit 4
	fi

	if ! which python3 > /dev/null 2>&1; then
		echo "selftests: [SKIP] Could not run test without python3"
		exit 4
	fi

	python_major=`python3 -c "import sys; print(sys.version_info[0])"`
	python_minor=`python3 -c "import sys; print(sys.version_info[1])"`
	if [[ python_major -lt 3 || ( python_major -eq 3 && python_minor -lt 9 ) ]] ; then
		echo "selftests: [SKIP] Could not run test without at least python3.9"
		python3 -V
		exit 4
	fi
}

check_env

#if we are running in a python environment, we need to capture that
#python bin so we can use the same python environment in the vm
PY_CMD=`which python3`

LOG_DIR=/tmp/rds_logs
mkdir -p  $LOG_DIR

# start a VM using a 9P root filesystem that maps to the host's /
# we pass ./init.sh from the same directory as we are in as the
# guest's init, which will run the tests and copy the coverage
# data back to the host filesystem.
$QEMU_BINARY \
	-enable-kvm \
	-cpu host \
	-smp 4 \
	-kernel arch/x86/boot/bzImage \
	-append "rootfstype=9p root=/dev/root rootflags=trans=virtio,version=9p2000.L rw console=ttyS0 init=${current_dir}/init.sh -d ${LOG_DIR} -p ${PY_CMD}" \
	-display none \
	-serial stdio \
	-fsdev local,id=fsdev0,path=/,security_model=none \
	-device virtio-9p-pci,fsdev=fsdev0,mount_tag=/dev/root \
	-no-reboot

# generate a nice HTML coverage report
echo running gcovr...
gcovr -v -s --html-details -o $LOG_DIR/coverage/  net/rds/
