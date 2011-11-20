PYTHON = python
SETUP = $(PYTHON) setup.py
PYDOCTOR ?= pydoctor
ifeq ($(shell $(PYTHON) -c "import sys; print(sys.version_info[0:2] >= (3, 0))"),True)
VALID = TRUE
endif
TESTRUNNER ?= unittest
RUNTEST = PYTHONPATH=.:$(PYTHONPATH) $(PYTHON) -m $(TESTRUNNER)

all: build

doc:: pydoctor

pydoctor::
	$(PYDOCTOR) --make-html -c dulwich.cfg

build:: is_valid
	$(SETUP) build
	$(SETUP) build_ext -i

install:: is_valid
	$(SETUP) install

check:: build
	$(RUNTEST) dulwich.tests.test_suite

check-nocompat:: build
	$(RUNTEST) dulwich.tests.nocompat_test_suite

check-noextensions:: clean
	$(RUNTEST) dulwich.tests.test_suite

check-all: check check-noextensions

clean::
	$(SETUP) clean --all
	rm -f dulwich/*.so
	rm -f dulwich/*.pyc
	rm -f dulwich/tests/*.pyc
	rm -f dulwich/tests/compat/*.pyc
	rm -rf dulwich/__pycache__
	rm -rf dulwich/tests/__pycache__
	rm -rf dulwich/tests/compat/__pycache__

is_valid:
ifndef VALID
	@echo "Invalid version of python detected. This library requires python version 3.x or higher"
	@exit 2
endif
