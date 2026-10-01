#-----------------------------------------------------------------------------
set(MODULE_NAME AREG)

#-----------------------------------------------------------------------------
# AREG_Method/ and Resources/UI/AREG.ui belonged to the former local module:
# they built the argument dictionaries for the AREG_CBCT / AREG_IOS /
# AREG_IOSCBCT CLIs and chained them with AMASSS, ASO, ALI and CrownSeg from
# inside Slicer, via a conda environment and a pytorch3d wheel. The module is
# now a thin client of the server's "AREG" tool and builds its panel from
# GET /tools, so neither is shipped any more. AREG_Method/ is left in the tree,
# the same way ASO_Method and ALI_Method were -- see ARCHITECTURE.md.
#
# The .ui went further and was DELETED, as ASO's was. A resource that is
# neither listed below nor read by any code still looks like the panel to
# whoever opens Qt Designer, so editing it is a no-op that reports no error --
# and it cost a day of looking for a missing UI change on the wrong side of the
# client/server line. There is nothing here to edit: the panel comes from the
# schema.
set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
)

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/${MODULE_NAME}.png
)

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
)

#-----------------------------------------------------------------------------
if (BUILD_TESTING)

  # Register the unittest subclass in the main script as a ctest.
  # Note that the test will also be available at runtime.
  slicer_add_python_unittest(SCRIPT ${MODULE_NAME}.py)

  # Additional build-time testing
  add_subdirectory(Testing)
endif ()
