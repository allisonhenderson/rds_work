#! /bin/bash

set -e
set -u
set -x

here="$(realpath "$(dirname "$0")")"

# start a VM using a 9P root filesystem that maps to the host's /
# we pass ./init.sh from the same directory as we are in as the
# guest's init, which will run the tests and copy the coverage
# data back to the host filesystem.
/usr/libexec/qemu-kvm \
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
gcovr --html-details -o coverage/ net/rds/
