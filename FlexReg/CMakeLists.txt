#-----------------------------------------------------------------------------
set(MODULE_NAME FlexReg)

#-----------------------------------------------------------------------------

# FlexRegLib holds the patch PREVIEW, which stays on this machine: dragging a
# pad recomputes the outline in about 18 ms, and a round trip per gesture is not
# a preview. The patch and the registration themselves run on the server, so
# install_pytorch.py is gone with the local torch it existed to fetch.
set(FOLDER_LIBRARY FlexRegLib)

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/${MODULE_NAME}.png
  )

set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  FlexRegLib/__init__.py
  FlexRegLib/butterfly_preview.py
  FlexRegLib/orientation.py
  FlexRegLib/transform.py
  FlexRegLib/util.py
)

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
  )

#-----------------------------------------------------------------------------
if(BUILD_TESTING)

  # Register the unittest subclass in the main script as a ctest.
  # Note that the test will also be available at runtime.
  slicer_add_python_unittest(SCRIPT ${MODULE_NAME}.py)

  # Additional build-time testing
  add_subdirectory(Testing)
endif()
