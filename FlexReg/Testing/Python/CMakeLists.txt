
#slicer_add_python_unittest(SCRIPT ${MODULE_NAME}ModuleTest.py)

#-----------------------------------------------------------------------------
# test_flexreg_client.py builds the panel from FlexReg's own GET /tools schema
# against the qt/ctk/slicer stand-ins in ServerToolsCore/Testing/Python. Pure
# Python once the widgets are stubbed, so it is a plain Python3 ctest rather
# than a Slicer interpreter launch. Same rationale as AREG's.
find_package(Python3 COMPONENTS Interpreter REQUIRED)

add_test(
  NAME FlexReg_test_flexreg_client
  COMMAND ${Python3_EXECUTABLE} -m unittest test_flexreg_client
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )
