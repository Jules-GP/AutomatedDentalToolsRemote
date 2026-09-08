#-----------------------------------------------------------------------------
# test_client.py only imports ServerToolsCoreLib.client (requests + stdlib, no
# slicer/qt), so it is registered as a plain Python3 ctest rather than through
# slicer_add_python_unittest - no need to pay for a full Slicer interpreter
# launch to test an HTTP client. See ARCHITECTURE.md, "Debatable decisions".
find_package(Python3 COMPONENTS Interpreter REQUIRED)

add_test(
  NAME ServerToolsCore_test_client
  COMMAND ${Python3_EXECUTABLE} -m unittest test_client
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )

# test_transfer.py drives the real chunked upload / ranged download against a
# stdlib ThreadingHTTPServer on a loopback socket, no mocks, because what that
# module does is concurrency and byte offsets. Same reasoning as above: requests
# + stdlib only, so no Slicer interpreter is needed. It binds an ephemeral
# 127.0.0.1 port and never reaches the network.
add_test(
  NAME ServerToolsCore_test_transfer
  COMMAND ${Python3_EXECUTABLE} -m unittest test_transfer
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )

# test_formgen.py does need qt/ctk/slicer, but only as the small stand-ins in
# qt_stubs.py: it checks which widgets a schema produces and what they read
# back as, which is pure Python once the widget classes are stubbed. Same
# rationale as above - no Slicer interpreter launch needed.
add_test(
  NAME ServerToolsCore_test_formgen
  COMMAND ${Python3_EXECUTABLE} -m unittest test_formgen
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )

# test_hosted_test_files.py: what happens when a user picks one of a tool's
# server-hosted test files -- the download, the cache, the unpacking and what
# reaches the scene. Same stubs plus a handful of slicer.util functions, and a
# BackgroundJob stand-in running on a real thread, since "this must not block
# the Slicer window" is the property under test.
add_test(
  NAME ServerToolsCore_test_hosted_test_files
  COMMAND ${Python3_EXECUTABLE} -m unittest test_hosted_test_files
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )

# test_joystick.py: the 2D pad's value/geometry logic under the same stubs.
# Painting aside, the pad is plain arithmetic.
add_test(
  NAME ServerToolsCore_test_joystick
  COMMAND ${Python3_EXECUTABLE} -m unittest test_joystick
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )
