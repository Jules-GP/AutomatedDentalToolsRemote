#-----------------------------------------------------------------------------
set(MODULE_NAME BATCHDENTALSEG)

#-----------------------------------------------------------------------------
# Testing/ is NOT shipped. No code here imports it: it came with the local module
# whose inference it tested, and that inference is the server's now. It would
# also install as a top-level `Testing` package in qt-scripted-modules, which is
# a generic name to occupy for files nothing reads.
set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
)

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/DentalSegmentator.png
  Resources/Icons/DentalSegmentator_full_icon.png
  Resources/Icons/DentalSegmentator_full_icon_2.png
  Resources/Icons/info.png
  Resources/Icons/loading.gif
  Resources/Icons/start_icon.png

)

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
)
#-----------------------------------------------------------------
