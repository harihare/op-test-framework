#!/usr/bin/env python3
#
# OpenPOWER Automated Test Project
#
# Contributors Listed Below - COPYRIGHT 2017
# [+] International Business Machines Corp.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied. See the License for the specific language governing
# permissions and limitations under the License.

"""
The OpExpect module is a wrapper around the standard Python pexpect module
that will *always* look for certain error conditions for OpenPOWER machines.

This is to enable op-test test cases to fail *quickly* in the event of errors
such as kernel panics, RCU stalls, machine checks, firmware crashes etc.

In the event of error, the failure_callback function will be called, which
typically will be set up to set the machine state to UNKNOWN, so that when
the next test starts executing, we re-IPL the system to get back to a clean
slate.

When developing test cases, use OpExpect over pexpect. If you *intend* for
certain error conditions to occur, you can catch the exceptions that OpExpect
throws.
"""

import pexpect
import logging

from .exceptions import *

log = logging.getLogger(__name__)

def handle_kernel_err(pty, p):
    log = str(pty.after)
    l = 0

    # scrape the reset of the error message
    while l != 8:
        l = super(spawn, pty).expect([
                 "INFO: rcu_sched self-detected stall on CPU",
                 "Watchdog .* Hard LOCKUP",
                 "Sending IPI to other CPUs",
                 ":mon>",
                 "Rebooting in \\d+ seconds",
                 "Kernel panic - not syncing: Fatal exception",
                 "Kernel panic - not syncing: Hard LOCKUP",
                 "opal_cec_reboot2", pexpect.TIMEOUT,
                 ], timeout=15)
        log = log + str(pty.before) + str(pty.after)
        if l in [2, 3, 4, 7]:
            # We know we have the end of the error message, so let's
            # stop here.
            break

    if "soft lockup" in p:
        raise KernelSoftLockup(log)
    if "hard lockup" in p.tolower():
        raise KernelHardLockup(log)
    if "kernel BUG at" in p:
        raise KernelBug(log)
    if "kernel panic" in p.tolower():
        if l == 2:
            raise KernelKdump(log)
        if l == 7:
            raise KernelFADUMP(log)
        raise KernelPanic(log)
    if "Oops" in p:
        raise KernelOOPS(log)

    raise KernelCrashUnknown(log)

def handle_opal_err(pty, is_assert):
    log = pty.after

    # hmm,  might need to move this elsewhere
    l = super(spawn, pty).expect(["boot_entry.*\r\n",
                                   "Initiated MPIPL",
                                   "NVRAM: Failed to load", pexpect.TIMEOUT],
                                   timeout=10)
    log = log + pty.before + pty.after
    if is_assert:
       raise SkibootAssert(log)
    else:
        raise SkibootException(log)


def handle_plat_err(pty, exp):
    # Reboot due to HW core checkstop
    # Let's attempt to capture Hostboot output
    log = pty.before + pty.after
    try:
        l = super(spawn, pty).expect("================================================",
                                      timeout=120)
        log = log + pty.before + pty.after
        l = super(spawn, pty).expect(
            "System checkstop occurred during runtime on previous boot", timeout=30)
        log = log + pty.before + pty.after
        l = super(spawn, pty).expect("================================================",
                                      timeout=60)
        log = log + pty.before + pty.after
        l = super(spawn, pty).expect("ISTEP", timeout=20)
        log = log + pty.before + pty.after
    except pexpect.TIMEOUT as t:
        pass

    raise PlatformError(log)

def qemu_err(pty):
    raise CommandFailed(pty.command, "????", -1)


import sys

class spawn(pexpect.spawn):
    def __init__(self, command, args=[], maxread=8000,
                 searchwindowsize=None, logfile=sys.stdout, cwd=None, env=None,
                 ignore_sighup=False, echo=True, preexec_fn=None,
                 encoding='utf-8', codec_errors='ignore', dimensions=None,
                 failure_callback=None, failure_callback_data=None):

        # deprecated
        assert not failure_callback
        assert not failure_callback_data

        self.logfile = logfile

        self.command = command
        super(spawn, self).__init__(command, args=args,
                                    maxread=maxread,
                                    searchwindowsize=searchwindowsize,
                                    logfile=self.logfile,
                                    cwd=cwd, env=env,
                                    ignore_sighup=ignore_sighup,
                                    encoding=encoding,
                                    codec_errors=codec_errors)
        self.patterns = []
        if not self.isalive():
            raise Exception("im dead")

    # XXX: this seems odd
    def get_capture(self):
        return self.logtee.get_capture()
    def start_capture(self):
        return self.logtee.start_capture()
    def stop_capture(self):
        return self.logtee.stop_capture()

    def add_pattern(self, cb, pattern):
        self.patterns.append(pattern)
        self.cb.append(cb)

    def clear_patterns(self):
        self.patterns = []
        self.cb = []

    def zap(self):
        try:
            buf = self.read_nonblocking(16384, timeout = 1)
            log.debug("zapping: {}".format(buf))
        except pexpect.exceptions.TIMEOUT:
            pass
        return

    def expect(self, input_pattern, timeout=-1, searchwindowsize=-1):
        # HACK: just for now until I move this into OpTestSystem
        # FIXME: We *really* need to do that soon
        self.clear_patterns()

        qemu_err = lambda pty, pat: qemu_err(pty, pat)
        self.add_pattern(qemu_err, "qemu: could find kernel")

        kern_err = lambda pty, pat: handle_kernel_err(pty, pat)
        self.add_pattern(kern_err, "INFO: rcu_sched self-detected stall on CPU")
        self.add_pattern(kern_err, "kernel BUG at")
        self.add_pattern(kern_err, "Kernel panic")
        self.add_pattern(kern_err, "Oops: Kernel access of bad area")

        self.add_pattern(kern_err, "Watchdog .* Hard LOCKUP")
        self.add_pattern(kern_err, "watchdog: .* detected hard LOCKUP on other CPUs")
        self.add_pattern(kern_err, "Watchdog .* detected Hard LOCKUP other CPUS")
        self.add_pattern(kern_err, "watchdog: BUG: soft lockup")

        opal_assert = lambda pty, pat: handle_opal_err(pty, True)
        self.add_pattern(opal_assert, "\\[[0-9. ]+,0\\] Assert fail:")            # old assert
        self.add_pattern(opal_assert, "\\[[0-9. ]+,[0-4]\\] < assert failed at ") # mouse assert

        opal_ex = lambda pty, pat: handle_opal_err(pty, False)
        self.add_pattern(opal_ex, "\\[[0-9. ]+,[0-9]\\] Unexpected exception")
        self.add_pattern(opal_ex, "OPAL exiting with locks held")
        self.add_pattern(opal_ex, "LOCK ERROR: Releasing lock we don't hold")

        plat_err = lambda pty, pat: handle_plat_err(pty, pat)
        self.add_pattern(plat_err, "OPAL: Reboot requested due to Platform error.")


        # FIXME: fix this once the above is done
        if isinstance(input_pattern, list):
            patterns = self.patterns + input_pattern
            cb = self.cb + [None for x in input_pattern]
        else:
            patterns = self.patterns + [input_pattern]
            cb = self.cb + [None]

        r = super(spawn, self).expect(patterns,
                                      timeout=timeout,
                                      searchwindowsize=searchwindowsize)

        if r in [pexpect.EOF, pexpect.TIMEOUT]:
            return r


        # call the callback for this pattern, assuming we have one
        if cb[r]:
            cb[r](self, patterns[r]) # this should throw
            raise Exception("OpExpect callback returned, don't do that")

        return r - len(self.patterns)

