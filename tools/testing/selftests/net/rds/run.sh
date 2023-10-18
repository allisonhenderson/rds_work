#! /bin/bash

set -e
set -u

here="$(realpath "$(dirname "$0")")"

find_qemu() {
	# some systems have qemu/kvm in a weird location
	# let's just try a few different ones

	for qemu in qemu-system-x86_64 kvm /usr/libexec/qemu-kvm
	do
		command -v $qemu && return
	done

	echo "error: qemu not found" >&2
	exit 1
}

qemu=$(find_qemu)

# start a VM using a 9P root filesystem that maps to the host's /
# we pass ./init.sh from the same directory as we are in as the
# guest's init, which will run the tests and copy the coverage
# data back to the host filesystem.
$qemu \
	-enable-kvm \
	-cpu host \
	-kernel arch/x86/boot/bzImage \
	-append "rootfstype=9p root=/dev/root rootflags=trans=virtio,version=9p2000.L rw console=ttyS0 init=${here}/init.sh" \
	-display none \
	-serial stdio \
	-fsdev local,id=fsdev0,path=/,security_model=none \
	-device virtio-9p-pci,fsdev=fsdev0,mount_tag=/dev/root \
	-no-reboot

# generate a nice HTML coverage report
echo running gcovr...
gcovr --html-details -o coverage/ net/rds/
