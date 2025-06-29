#include <Python.h>
#include <pxr/usd/usd/stage.h>
#include <pxr/usd/usd/primRange.h> // For traversing prims
#include <pxr/usd/usdGeom/gprim.h> // For checking if a prim is a geometric primitive
#include <iostream>
#include <vector>

// Test function that will be callable from Python
static PyObject* hello_world(PyObject* self, PyObject* args) {
    std::cout << "Hello from C++! Checking USD..." << std::endl;

    // Create a dummy USD stage in memory to verify linking
    pxr::UsdStageRefPtr stage = pxr::UsdStage::CreateInMemory();
    if (stage) {
        std::cout << "Successfully created a USD stage in memory." << std::endl;
    } else {
        std::cout << "Failed to create a USD stage." << std::endl;
    }

    Py_RETURN_NONE;
}

// New function to import a USD file and return prim info
static PyObject* import_usd(PyObject* self, PyObject* args) {
    const char* filepath;
    if (!PyArg_ParseTuple(args, "s", &filepath)) {
        return NULL; // Python exception will be set
    }

    std::cout << "C++: Opening USD file: " << filepath << std::endl;
    pxr::UsdStageRefPtr stage = pxr::UsdStage::Open(filepath);

    if (!stage) {
        PyErr_SetString(PyExc_IOError, "Failed to open USD stage.");
        return NULL;
    }

    PyObject* prim_list = PyList_New(0);

    for (const auto& prim : stage->Traverse()) {
        if (prim.IsA<pxr::UsdGeomGprim>()) {
            PyObject* prim_info = PyDict_New();
            
            // Add name
            std::string name = prim.GetName().GetString();
            PyDict_SetItemString(prim_info, "name", PyUnicode_FromString(name.c_str()));

            // Add type
            std::string type = prim.GetTypeName().GetString();
            PyDict_SetItemString(prim_info, "type", PyUnicode_FromString(type.c_str()));

            PyList_Append(prim_list, prim_info);
            Py_DECREF(prim_info);
        }
    }

    return prim_list;
}

// Method definition object
static PyMethodDef RemixNativeMethods[] = {
    {"hello", hello_world, METH_NOARGS, "Prints a hello message from C++ and tests USD."},
    {"import_usd", import_usd, METH_VARARGS, "Imports a USD file and returns a list of its geometric prims."},
    {NULL, NULL, 0, NULL}
};

// Module definition structure
static struct PyModuleDef remix_native_module = {
    PyModuleDef_HEAD_INIT,
    "remix_native", // Module name
    "A native C++ extension for the RTX Remix Toolkit.", // Module description
    -1,
    RemixNativeMethods
};

// Module initialization function
PyMODINIT_FUNC PyInit_remix_native(void) {
    return PyModule_Create(&remix_native_module);
} 