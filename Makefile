# Development tasks. Every tool runs through uv, from the dev dependency group,
# so `make` needs nothing installed but uv.

### Tools ###########################################################
SED=sed
UV=uv
UV_RUN=$(UV) run --group dev
PACKAGE=mstamat-claude-helpers

# Use bash instead of sh, and stop a recipe at its first failing command.
SHELL=bash
.SHELLFLAGS=-ec


### First rule - called automatically ###############################
.PHONY: all

all: help
##-


##-### Installation ##################################################
.PHONY: tool-install tool-upgrade

# NB: uv installs a tool into a private environment of its own, resolved from
#     the version ranges in pyproject.toml. uv.lock plays no part in it, so the
#     installed helpers can run newer dependencies than the tests did.
#     The install is editable: code changes take effect on the next run, and a
#     change to pyproject.toml (the version, a new command, a dependency) needs
#     tool-install again. The dependencies move only with tool-upgrade.
tool-install:	##- install the helpers from this clone as uv tools; rerun after a change to pyproject.toml
	$(UV) tool install --editable .

tool-upgrade:	##- move the installed helpers' dependencies to the newest versions pyproject.toml allows (not uv.lock)
	$(UV) tool upgrade $(PACKAGE)
##-


##-### Development ###################################################
.PHONY: build fmt lint tc

build:	##- build the sdist and the wheel into dist/
	$(UV) build

fmt:	##- format the Python code (ruff format)
	$(UV_RUN) ruff format

lint:	##- lint the Python code (ruff check)
	$(UV_RUN) ruff check

tc:	##- type-check the code and the tests (ty)
	$(UV_RUN) ty check src tests


##-### Dependencies ##################################################
.PHONY: uv-force uv-outdated uv-upgrade uv-upgrade-all

# NB: Patterns in .PHONY are not expanded, and naming a pattern's targets there
#     leaves them without a recipe, so the pattern rule below depends on this
#     phony target to stay out of date instead.
uv-force:

uv-outdated:	##- list the locked packages that have newer versions, with what needs them
	$(UV) tree --outdated --all-groups --depth 1

uv-upgrade:	##- upgrade the packages named in UV_UPGRADE in uv.lock, for example UV_UPGRADE=ruff
	$(if $(UV_UPGRADE),,$(error set UV_UPGRADE to the packages to upgrade))
	$(UV) lock $(addprefix --upgrade-package ,$(UV_UPGRADE))

uv-upgrade-%: uv-force	##- upgrade the packages of dependency group %, for example uv-upgrade-dev
	$(UV) lock --upgrade-group $(*)

# NB: uv cannot upgrade the [project] dependencies as a group, only by name
#     (uv-upgrade) or together with everything else (uv-upgrade-all).
uv-upgrade-all:	##- upgrade every package in uv.lock
	$(UV) lock --upgrade
##-


##-### Misc. #########################################################
.PHONY: help

help:	##- show this help
	@$(SED) -e '/#\{2\}-/!d; s/\\$$//; s/:[^#\t]*/:\t/; s/#\{2\}- *//' $(MAKEFILE_LIST)
