.PHONY: help build-binary clean-binary rebuild-binary

help:
	@echo "Available targets:"
	@echo "  build-binary   Build tm1gitpy executable with PyInstaller"
	@echo "  clean-binary   Remove previous PyInstaller build artifacts"
	@echo "  rebuild-binary Clean and then build the executable"

clean-binary:
	rm -rf build dist

build-binary:
	python -m PyInstaller --clean tm1gitpy.spec

rebuild-binary: clean-binary build-binary
