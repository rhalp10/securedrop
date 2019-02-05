import pytest
import re


KERNEL_VERSION = pytest.securedrop_test_vars.grsec_version


def test_ssh_motd_disabled(host):
    """
    Ensure the SSH MOTD (Message of the Day) is disabled.
    Grsecurity balks at Ubuntu's default MOTD.
    """
    f = host.file("/etc/pam.d/sshd")
    assert f.is_file
    assert not f.contains("pam\.motd")


@pytest.mark.parametrize("package", [
    'linux-firmware-image-{}-grsec'.format(KERNEL_VERSION),
    'linux-image-{}-grsec'.format(KERNEL_VERSION),
    'paxctl',
    'securedrop-grsec',
])
def test_grsecurity_apt_packages(host, package):
    """
    Ensure the grsecurity-related apt packages are present on the system.
    Includes the FPF-maintained metapackage, as well as paxctl, for managing
    PaX flags on binaries.
    """
    assert host.package(package).is_installed


@pytest.mark.parametrize("package", [
    'linux-signed-image-generic-lts-utopic',
    'linux-signed-image-generic',
    'linux-signed-generic-lts-utopic',
    'linux-signed-generic',
    '^linux-image-.*generic$',
    '^linux-headers-.*',
])
def test_generic_kernels_absent(host, package):
    """
    Ensure the default Ubuntu-provided kernel packages are absent.
    In the past, conflicting version numbers have caused machines
    to reboot into a non-grsec kernel due to poor handling of
    GRUB_DEFAULT logic. Removing the vendor-provided kernel packages
    prevents accidental boots into non-grsec kernels.
    """
    # Can't use the TestInfra Package module to check state=absent,
    # so let's check by shelling out to `dpkg -l`. Dpkg will automatically
    # honor simple regex in package names.
    c = host.run("dpkg -l {}".format(package))
    assert c.rc == 1
    error_text = "dpkg-query: no packages found matching {}".format(package)
    assert c.stderr == error_text


def test_grsecurity_lock_file(host):
    """
    Ensure system is rerunning a grsecurity kernel by testing for the
    `grsec_lock` file, which is automatically created by grsecurity.
    """
    f = host.file("/proc/sys/kernel/grsecurity/grsec_lock")
    assert oct(f.mode) == "0600"
    assert f.user == "root"
    assert f.size == 0


def test_grsecurity_kernel_is_running(host):
    """
    Make sure the currently running kernel is specific grsec kernel.
    """
    c = host.run('uname -r')
    assert c.stdout.endswith('-grsec')
    assert c.stdout == '{}-grsec'.format(KERNEL_VERSION)


@pytest.mark.parametrize('sysctl_opt', [
  ('kernel.grsecurity.grsec_lock', 1),
  ('kernel.grsecurity.rwxmap_logging', 0),
  ('vm.heap_stack_gap', 1048576),
])
def test_grsecurity_sysctl_options(host, sysctl_opt):
    """
    Check that the grsecurity-related sysctl options are set correctly.
    In production the RWX logging is disabled, to reduce log noise.
    """
    with host.sudo():
        assert host.sysctl(sysctl_opt[0]) == sysctl_opt[1]


@pytest.mark.parametrize('paxtest_check', [
  "Executable anonymous mapping",
  "Executable bss",
  "Executable data",
  "Executable heap",
  "Executable stack",
  "Executable shared library bss",
  "Executable shared library data",
  "Executable anonymous mapping (mprotect)",
  "Executable bss (mprotect)",
  "Executable data (mprotect)",
  "Executable heap (mprotect)",
  "Executable stack (mprotect)",
  "Executable shared library bss (mprotect)",
  "Executable shared library data (mprotect)",
  "Writable text segments",
  "Return to function (memcpy)",
  "Return to function (memcpy, PIE)",
])
def test_grsecurity_paxtest(host, paxtest_check):
    """
    Check that paxtest does not report anything vulnerable
    Requires the package paxtest to be installed.
    The paxtest package is currently being installed in the app-test role.
    """
    if host.exists("/usr/bin/paxtest"):
        with host.sudo():
            c = host.run("paxtest blackhat")
            assert c.rc == 0
            assert "Vulnerable" not in c.stdout
            regex = "^{}\s*:\sKilled$".format(re.escape(paxtest_check))
            assert re.search(regex, c.stdout)


def test_grub_pc_marked_manual(host):
    """
    Ensure the `grub-pc` packaged is marked as manually installed.
    This is necessary for VirtualBox with Vagrant.
    """
    c = host.run('apt-mark showmanual grub-pc')
    assert c.rc == 0
    assert c.stdout == "grub-pc"


def test_apt_autoremove(host):
    """
    Ensure old packages have been autoremoved.
    """
    c = host.run('apt-get --dry-run autoremove')
    assert c.rc == 0
    assert "The following packages will be REMOVED" not in c.stdout


@pytest.mark.xfail(strict=True,
                   reason="PaX flags unset at install time, see issue #3916")
@pytest.mark.parametrize("binary", [
    "/usr/sbin/grub-probe",
    "/usr/sbin/grub-mkdevicemap",
    "/usr/bin/grub-script-check",
])
def test_pax_flags(host, binary):
    """
    Ensure PaX flags are set correctly on critical Grub binaries.
    These flags are maintained as part of a post-install kernel hook
    in the `securedrop-grsec` metapackage. If they aren't set correctly,
    the machine may fail to boot into a new kernel.
    """

    f = host.file("/etc/kernel/postinst.d/paxctl-grub")
    assert f.is_file
    assert f.contains("^paxctl -zCE {}".format(binary))

    c = host.run("paxctl -v {}".format(binary))
    assert c.rc == 0

    assert "- PaX flags: --------E--- [{}]".format(binary) in c.stdout
    assert "EMUTRAMP is enabled" in c.stdout
    # Tracking regressions; previous versions of the Ansible config set
    # the "p" and "m" flags.
    assert "PAGEEXEC is disabled" not in c.stdout
    assert "MPROTECT is disabled" not in c.stdout


@pytest.mark.parametrize('kernel_opts', [
  'WLAN',
  'NFC',
  'WIMAX',
  'WIRELESS',
  'HAMRADIO',
  'IRDA',
  'BT',
])
def test_wireless_disabled_in_kernel_config(host, kernel_opts):
    """
    Kernel modules for wireless are blacklisted, but we go one step further and
    remove wireless support from the kernel. Let's make sure wireless is
    disabled in the running kernel config!
    """

    kernel_config_path = "/boot/config-{}-grsec".format(KERNEL_VERSION)
    kernel_config = host.file(kernel_config_path).content_string

    line = "# CONFIG_{} is not set".format(kernel_opts)
    assert line in kernel_config
