#-----------------------------------------------------------------------------
set(MODULE_NAME Agent_CLI)
set(FOLDER_LIBRARY Agent_CLI_utils)

set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  ${FOLDER_LIBRARY}/__init__.py
  ${FOLDER_LIBRARY}/utils.py
  ${FOLDER_LIBRARY}/parameter_extraction_improved.py
  ${FOLDER_LIBRARY}/parameter_validator.py
  )

# ${MODULE_NAME}.xml has to land NEXT TO ${MODULE_NAME}.py, not under Resources/,
# because that pairing is what makes Slicer register this as a scripted CLI --
# the .py holds no module class, so without the .xml beside it Slicer falls back
# to running the file as an executable and the module fails to instantiate. It
# worked in a source tree only because the .xml sits there already. Resources
# keep their relative path, so an entry with no directory prefix installs beside
# the script, the way manifest.yaml already does.
set(MODULE_PYTHON_RESOURCES
  ${MODULE_NAME}.xml
  Resources/Icons/${MODULE_NAME}.png
  Resources/UI/${MODULE_NAME}.ui
  manifest.yaml
  )

SlicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
)
