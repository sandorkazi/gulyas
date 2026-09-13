# Generated from environment/templates/python.json — do not hand-edit.
# Re-render with: bash environment/scripts/env-setup (choose render) or --render-all
noprofile
noroot
nonewprivs
seccomp
seccomp.block-secondary
caps.drop all
machine-id
private-dev
private-tmp
private-etc passwd,group,hostname,hosts,resolv.conf,ssl,ca-certificates,crypto-policies,pki,nsswitch.conf
dbus-user none
dbus-system none
nogroups
nosound
notv
nox11
nodvd
disable-mnt
read-only ${HOME}/.config/herdr
blacklist ${HOME}/.ssh
blacklist ${HOME}/.gnupg
blacklist ${HOME}/.aws
blacklist ${HOME}/.config/gh
blacklist ${HOME}/.config/herdr
noblacklist ${HOME}/.herdr/worktrees
whitelist /usr
whitelist /bin
whitelist ${HOME}/.cache
whitelist ${HOME}/.cache/pip
whitelist ${HOME}/.cache/uv
whitelist ${HOME}/go/pkg/mod
read-only ${HOME}/go/pkg/mod
memory-deny-write-execute
restrict-namespaces
