import pytest
import optest

from optest.qemu import QemuSystem
from optest.petitboot import PetitbootHelper

import misc

def test_qemu_boot_nokernel(qemu):

    # HACK: zap the kernel (and pnor) so we crash at boot
    qemu.kernel = None
    qemu.pnor = None

    qemu.host_power_on()

    with pytest.raises(optest.SkibootAssert):
        qemu.boot_to('petitboot') # should fail since there's no kernel image
    qemu.host_power_off()

def test_qemu_boot_pb(qemu):
    qemu.boot_to('petitboot') # should fail with a timeout

    pb = PetitbootHelper(qemu)
    pb.goto_shell()

    qemu.run_command("echo hi")
