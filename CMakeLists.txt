cmake_minimum_required(VERSION 3.16.3...3.19.7 FATAL_ERROR)

project(AutomatedDentalToolsRemote)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalToolsRemote/blob/main/README.md")
set(EXTENSION_CATEGORY "Orthodontics")
set(EXTENSION_CONTRIBUTORS "Maxime Gillot (University of Michigan), Baptiste Baquero (UoM), Juan Carlos Prieto (University of North Carolina), Nathan Hutin (UoM), Luc Anchling (UoM), Jeanne Claret (UoM), Gaelle Leroux(UoM) Lucie Dole (University of North Carolina)")
set(EXTENSION_DESCRIPTION "Cloud client for Automated Dental Tools: each module is a thin panel over a remote inference server, so a laptop that cannot run a segmentation can still ask for one. Nothing is installed into Slicer and no model is downloaded.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/DCBIA-OrthoLab/SlicerAutomatedDentalToolsRemote/main/SlicerAutomaticTools.png")
set(EXTENSION_SCREENSHOTURLS "https://raw.githubusercontent.com/DCBIA-OrthoLab/SlicerAutomatedDentalToolsRemote/main/ADT-exemple.png")
# No extension dependencies: every computation runs on the remote tool server,
# so this client needs neither SlicerConda, nor SlicerDentalModelSeg, nor NNUNet.
set(EXTENSION_DEPENDS "NA") # Specified as a list or "NA" if no dependencies

#-----------------------------------------------------------------------------
# Extension dependencies
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
# Extension modules
add_subdirectory(AMASSS)
add_subdirectory(ALI)
add_subdirectory(ASO)
add_subdirectory(AREG)
add_subdirectory(AutoMatrix)
add_subdirectory(AutoCrop3D)
add_subdirectory(FlexReg)
add_subdirectory(MRI2CBCT)
add_subdirectory(MRI2CBCT_CLI)
add_subdirectory(BATCHDENTALSEG)
add_subdirectory(DOCShapeAXI)
add_subdirectory(CLIC)
add_subdirectory(CNE)
add_subdirectory(CNE_CLI)
add_subdirectory(Medical_Data_Anonymizer_Module)
add_subdirectory(VFACE)
add_subdirectory(GreedyReg)
add_subdirectory(Agent)
add_subdirectory(Agent_CLI)
add_subdirectory(ServerToolsCore)
add_subdirectory(ServerToolsSettings)
add_subdirectory(SlicerCloud)
add_subdirectory(SurgMovPred)
add_subdirectory(Crown_Seg)
add_subdirectory(VISU)
add_subdirectory(ExampleTool)
# The CLI modules of the former local extension are deliberately NOT built.
# Nothing shipped here calls them: every remaining lookup of ALI_CBCT, ASO_*,
# AREG_* and VFACE_CLI lives in AREG/AREG_Method/ and VFACE/VFACE_utils/, which
# their own CMakeLists do not package either. The sequencing they existed for is
# the server's now, so building them would only add dead modules to the user's
# Slicer.
## NEXT_MODULE

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
