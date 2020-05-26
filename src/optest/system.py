#!/usr/bin/env python3
# IBM_PROLOG_BEGIN_TAG
# This is an automatically generated prolog.
#
# $Source: op-test-framework/common/OpTestSystem.py $
#
# OpenPOWER Automated Test Project
#
# Contributors Listed Below - COPYRIGHT 2015,2017
# [+] International Business Machines Corp.
#
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
#
# IBM_PROLOG_END_TAG

# @package OpTestSystem
#  System package for OpenPower testing.
#
#  This class encapsulates all interfaces and classes required to do end to end
#  automated flashing and testing of OpenPower systems.

import time
import pexpect

from . import logger
log = logger.optest_logger_glob.get_logger(__name__)

class UnsupportedStateError(Exception):
    pass
class ErrorPattern(Exception):
    pass
class MissedState(Exception):
    pass

class SysState():
    '''
    Defines one of the states a system can be in.

    We make a few assumptions about system states can work, namely:

    1. There's a linear flow of states. We always go through the previous
       states before entering the next one. This is a bit awkward for things
       like mpipl where there are unusual state transitions. However, it
       simplifies the general case so punting that work to the test case is
       probably a reasonable trade off.

    2. After powering on the host the system will boot automatically. The state
       machinery here just observes the boot process rather than driving it.

       The exception to the above is states which define an .action() function.
       That is used for things like the OS login prompt where some action is
       needed to continue the boot process.

        FIXME: hmm, we might be able to wrap that up in wait_entry(), maybe not since
               we want to support waitat()
    '''
    def __init__(self, name, entry_timeout, exit_timeout):
        self.name = name
        self.exit_timeout = exit_timeout
        self.entry_timeout = entry_timeout

    def __str__(self):
        return self.name

    def __hash__(self):
        return self.name.__hash__()

    def __eq__(self, other):
        if isinstance(other, SysState):
            return self.name == other.name
        return False

    # FIXME: Wonder if we should even bother with entry / exit stuff and
    # just have the one state handling function.
    #
    # FIXME: It's not clear to be how the state system should interact with cons
    # reconnect stuff. If the console dropped we can miss the state transitions
    # we're looking for. Old op-test handled this by "kicking" the console
    # on reconnect, so if we were in a wait state (petitboot menu, os login)
    # we could keep going. Even with that it's still possibly to miss
    # a transition since the default petitboot timeout isn't that long (10s)
    # and ipmitool won't notice the SOL flaking out instantly.
    def wait_entry(self, system):
        '''
        Polls the system to check if we're entered this state.

        Returns when the system is in this state.
        Raises BootError we time out waiting for the state, or some other error
        occurs.
        '''
        raise NotImplementedError()

    def wait_exit(self):
        '''
        Returns when we detect the system has left this state.

        Raises BootError if we time out waiting or some other error occurs
        '''
        raise NotImplementedError()

class ConsoleState(SysState):
    '''
    Many system states we can detect by just watching the system console. This
    helper implements a pile of expect logic to detect when we've entered into
    and exited a given state.
    '''
    def __init__(self, name,
                 entry_patterns, entry_timeout, exit_patterns, exit_timeout):
        self.entry_patterns = entry_patterns
        self.exit_patterns = exit_patterns
        super().__init__(name, entry_timeout, exit_timeout)

    def _watch_for(self, system, patterns, timeout):
        expect_table = list(patterns.keys())

        # FIXME: where's the right place to implement the console reconnect? possibly here...
        r = system.console.expect(expect_table, timeout=timeout)
        cb = patterns[expect_table[r]]
        if cb:
            raise Exception("hit error pattern") # FIXME: maybe we should... call the callback?

    def run(self, system, exit_at):
        self._watch_for(system, self.entry_patterns, self.entry_timeout)
        if exit_at:
            return False

        self._watch_for(system, self.exit_patterns, self.exit_timeout)
        return True

"""
class PetitbootState(ConsoleState):
    def wait_exit(self, system)
        ''' drives petitboot '''
        opt = system.conf.get('boot_option')

    def wait_entry(self, system):
        self._watch_for(system, pb_entry_patterns, self.entry_timeout)

    def wait_exit(self, system):
        '''
        Drives petitboot to the selected boot option
        opt = system.conf.get('boot_option')
        if opt:
        '''
        pass
"""

class BaseSystem(object):
    def __init__(self, conf=None, host=None, console=None, pdu=None):
        self.host = host
        self.console = console
        self.pdu = pdu

        # XXX: should setting this up be the job of the subclass? probably
        self.state_table = []
        self.last_state = None

        if conf and conf.get('power_off_delay'):
            self.power_off_delay = conf.get['power_off_delay']
        else:
            self.power_off_delay = 120

        log.debug("Initialised {}".format(self.__class__.__name__))

    ############################################################################
    # Power Control
    #
    # These are relatively low level functions that are intended for internal use
    # they just "do the thing" without any of the state machiney song and dance.
    #
    # classes inheriting OpTestSystem should implement these
    ############################################################################

    def host_power_on(self):
        raise NotImplementedError() # Turn the host power on
    def host_power_off(self):
        raise NotImplementedError() # Ask the OS to do a graceful shutdown
    def host_power_off_hard(self):
        raise NotImplementedError() # Remove host power

    def host_power_is_on(self): # -> Bool
        raise NotImplementedError()

    # we use this to check if the BMC is still usable or not
    # This should allow us to catch NC-SI induced headaches, etc
    # XXX: should we distingush between "alive" and "ready"? with openbmc we
    # can be responding to ping, but not ready to boot. Same with the FSP.
    def bmc_is_alive(self):
        raise NotImplementedError()

    # Assuming we have one...
    def pdu_power_on(self):
        raise NotImplementedError()
    def pdu_power_off(self):
        raise NotImplementedError()

    def collect_debug(self):
        raise NotImplementedError()

    def boot(self):
        # goto_state does a power off for us. Run until booted.
        self.goto_state(self.state_table[-1].name)

    def poweroff(self, softoff=True):
        ''' helper for powering off the host and reset our state tracking '''

        self.last_state = None

        # possibly excessive, but we've found some systems where it can take
        # a while for the BMC to work again due to NC-SI issues.
        if softoff:
            self.host_power_off()

            for i in range(self.power_off_delay):
                # the BMC can flake out while rebooting the host which causes
                # us to lose the console, etc.
                # the polling here will cause errors. Catch any exceptions that
                # crop up until we've hit the timeout.
                if self.bmc_is_alive() and not self.host_power_is_on():
                    return

                try:
                    if not self.host_power_is_on():
                        return

                    # run expect with no patterns so we get output during poweroff
                    # and so we catch any crashes that might happen while powering
                    # off
                    self.expect([pexpect.TIMEOUT], timeout=1)
                    log.info("Waiting for power off {}/{}s".format(i, self.power_off_delay))
                    print("Waiting for power off {}/{}s".format(i, self.power_off_delay))

                except Exception as e:
                    raise e

            log.info("Timeout while powering off host. Yanking power now")

        # try a little harder...
        self.host_power_off_hard()
        for i in range(self.power_yank_delay):
            if not self.host_power_is_on():
                return

            self.expect(timeout=1)

        # FIXME: use a precise exception type
        raise Exception("host hasn't turned off after yanking power")


    ############################################################################
    # Console Wrangling
    #
    # Returns the OpTestConsole for the host
    #
    ############################################################################

    # return the host console object
    def get_console(self):
        ''' returns the system's host console.

        NB: This always works, even if the host is off. Actual interactions
        throught the console require the host to be powered on though. Might
        seem obvious, but I'm putting it in writing so the expectation for
        simulated systems is clear. In the case of Qemu at least there's no
        underlying pty object unless qemu is actually running.
        '''

        return self.console

    def run_command(self, cmd, timeout=60):
        return self.console.run_command(cmd, timeout)

    def expect(self, params, timeout):
        return self.console.expect(params, timeout)

    ############################################################################
    #
    # System state tracking circus.
    #
    ############################################################################

    def _add_state(self, new_state):
        self.state_table.append(new_state)

    def _get_state(self, name):
        for s in self.state_table:
            if s.name == name:
                return s

        msg = "{} is not supported by this system type".format(target)
        raise UnsupportedStateError(msg)

    def assume_state(self, new_state_name):
        ''' Updates the state tracking machinery to reflect reality

        NB: You probably should use goto_state() rather than this. However,
            if you're doing something to change the underlying system state,
            such as forcing a reboot, then use this to sync the state
            tracking up with reality.
        '''
        self.last_state = self._get_state(new_state_name)

    def goto_state(self, target_name):
        target = self._get_state(target_name)

        log.debug('goto_state target {}'.format(target))
        self.poweroff()
        self.host_power_on()

        for s in self.state_table:
            self.assume_state(s.name)

            log.info('state {} - running'.format(s))

            if s == target:
                s.run(self, True);
                log.info("state {} - stopping, target reached".format(target))
                return

            s.run(self, False);
            log.info('state {} - done'.format(s.name))

# called when we get to the login prompt when we wanted petitboot (i.e. missed PB)
def error_pattern(pattern, context):
    raise ErrorPattern("pattern: {}, context: {}".format(pattern, value))

def missed_state(pattern, context):
    raise ErrorPattern("pattern: {}, context: {}".format(pattern, value))

# each expect table indicates when we've *entered* that state
sbe_entry= {
    'istep 4.' : None,              # SBE entry
}
sbe_exit = {
    'SBE starting hostboot' : None,
#    'shutdown requested': error_pattern, # FIXME: too broad, see GH issue
#TODO: find all the hostboot / SBE error patterns we might need to care about.
}

# each expect table indicates when we've *entered* that state
hb_entry= {
    'Welcome to Hostboot' : None,   # hostboot entry
    '|ISTEP 6.4' : None,
}
hb_exit = {
    'ISTEP 21. 3' : None, # host start payload
}

skiboot_entry = {
    '] OPAL v6.' : None,
    '] OPAL v5.' : None, #
    '] SkiBoot' : None,  # old boot header
    '] OPAL skiboot-v' : None, # occurs semi-frequently
}
skiboot_exit = {
    '] INIT: Starting kernel at' : None,
}

pb_entry = {
    'Petitboot': None,
    'x=exit': None,
    '/ #': None,
#    'shutdown requested': error_pattern, # FIXME: too broad, see GH issue
    'login: ': missed_state,
#    'mon> ': xmon_callback,
#    'dracut:/#': dracut_callback,
#    'System shutting down with error status': guard_callback,
    'Aborting!': error_pattern,
}
pb_exit = {
    'login: ': None,
    '/ #': error_pattern,
    'mon> ': error_pattern,
#    'dracut:/#': dracut_callback,
}

login_entry = {
    'login: ': None,
    '/ #': error_pattern,
    'mon> ': error_pattern,
#    'dracut:/#': dracut_callback,
}
login_exit = {
    '# ' : None,
    # FIXME: Add other shell patterns
}

class OpSystem(BaseSystem):
    # ordered list of possible states for this system
    openpower_state_table = [
#        ConsoleState('off',  None,           1),
        # there's a bit before we hit skiboot_entry
#        ConsoleState('sbe',       sbe_entry,     60, sbe_exit,      60),
        ConsoleState('hostboot',  hb_entry,      30, hb_exit,      180),
        ConsoleState('skiboot',   skiboot_entry, 30, skiboot_exit,  60),
#        PetitbootState('petitboot', pb_entry,      30, pb_exit,      120),
        ConsoleState('petitboot', pb_entry,      30, pb_exit,      120),
#        LoginState('login',       login_entry,   30, login_exit,   180),
#        ConsoleState('os',        os_entry,      10, os_exit,       30),
    ]

    def __init__(self, host=None, console=None, pdu=None):
        super().__init__(host, console, pdu)

        # build our state table
        for s in self.openpower_state_table:
            self._add_state(s)

        # a list of error patterns to look for while expect()ing the
        # host console FIXME: these are in OpExpect currently, which is
        # dumb
        self.error_patterns = []
