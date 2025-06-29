#include <Python.h>
#include <pxr/usd/usd/stage.h>
#include <pxr/usd/usd/primRange.h> // For traversing prims
#include <pxr/usd/usd/timeCode.h>            // For UsdTimeCode
#include <pxr/usd/usdGeom/gprim.h> // For checking if a prim is a geometric primitive
#include <pxr/usd/usdGeom/mesh.h>      // For UsdGeomMesh
#include <pxr/usd/usdGeom/primvarsAPI.h> // For reading primvars like UVs
#include <pxr/usd/usdGeom/xformable.h>       // For getting transforms
#include <pxr/usd/usdShade/materialBindingAPI.h> // For material bindings
#include <pxr/usd/usdShade/material.h>        // For materials
#include <pxr/usd/usdShade/shader.h>          // For shader nodes
#include <pxr/usd/sdf/assetPath.h>            // For texture asset paths
#include <pxr/base/vt/array.h>       // For VtArray
#include <pxr/base/gf/vec3f.h>       // For GfVec3f
#include <pxr/base/gf/vec2f.h>         // For UV coordinates
#include <pxr/usd/usdGeom/imageable.h>      // For checking visibility
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
        // Return None if the stage could not be opened
        Py_RETURN_NONE;
    }
    
    // This will be the list of mesh data dicts we return
    PyObject* meshes_list = PyList_New(0);

    // Traverse all prims in the stage
    for (const auto& prim : stage->Traverse()) {
        // Skip prims that are not visible
        pxr::UsdGeomImageable imageable(prim);
        if (imageable.ComputeVisibility(pxr::UsdTimeCode::Default()) == pxr::TfToken("invisible")) {
            continue;
        }

        // We only care about geometric primitives that can be rendered
        if (!prim.IsA<pxr::UsdGeomGprim>()) {
            continue;
        }
        
        // Skip abstract prims (definitions) and only process concrete prims (instances)
        if (prim.IsAbstract()) {
            continue;
        }

        if (!prim.IsA<pxr::UsdGeomMesh>()) {
            continue;
        }

        pxr::UsdGeomMesh mesh(prim);
        PyObject* mesh_dict = PyDict_New();

        // --- Transform ---
        pxr::UsdGeomXformable xformable(prim);
        pxr::GfMatrix4d transform = xformable.ComputeLocalToWorldTransform(pxr::UsdTimeCode::Default());
        PyObject* transform_list = PyList_New(16);
        const double* matrix_data = transform.GetArray();
        for (int i = 0; i < 16; ++i) {
            PyList_SET_ITEM(transform_list, i, PyFloat_FromDouble(matrix_data[i]));
        }
        PyDict_SetItemString(mesh_dict, "transform", transform_list);

        // --- Paths as Hashes ---
        PyDict_SetItemString(mesh_dict, "mesh_path", PyUnicode_FromString(prim.GetPath().GetText()));

        // --- Name ---
        PyDict_SetItemString(mesh_dict, "name", PyUnicode_FromString(prim.GetName().GetText()));

        // --- Vertices ---
        pxr::VtArray<pxr::GfVec3f> points;
        mesh.GetPointsAttr().Get(&points);
        PyObject* vertices_list = PyList_New(points.size() * 3);
        for (size_t i = 0; i < points.size(); ++i) {
            PyList_SET_ITEM(vertices_list, i * 3 + 0, PyFloat_FromDouble(points[i][0]));
            PyList_SET_ITEM(vertices_list, i * 3 + 1, PyFloat_FromDouble(points[i][1]));
            PyList_SET_ITEM(vertices_list, i * 3 + 2, PyFloat_FromDouble(points[i][2]));
        }
        PyDict_SetItemString(mesh_dict, "vertices", vertices_list);

        // --- Face Vertex Counts ---
        pxr::VtArray<int> face_counts;
        mesh.GetFaceVertexCountsAttr().Get(&face_counts);
        PyObject* counts_list = PyList_New(face_counts.size());
        for (size_t i = 0; i < face_counts.size(); ++i) {
            PyList_SET_ITEM(counts_list, i, PyLong_FromLong(face_counts[i]));
        }
        PyDict_SetItemString(mesh_dict, "face_vertex_counts", counts_list);

        // --- Face Vertex Indices ---
        pxr::VtArray<int> face_indices;
        mesh.GetFaceVertexIndicesAttr().Get(&face_indices);
        PyObject* indices_list = PyList_New(face_indices.size());
        for (size_t i = 0; i < face_indices.size(); ++i) {
            PyList_SET_ITEM(indices_list, i, PyLong_FromLong(face_indices[i]));
        }
        PyDict_SetItemString(mesh_dict, "face_vertex_indices", indices_list);

        // --- UVs (st coordinates) ---
        pxr::UsdGeomPrimvarsAPI primvarsAPI(mesh);
        pxr::UsdGeomPrimvar stPrimvar = primvarsAPI.GetPrimvar(pxr::TfToken("st"));
        if (stPrimvar) {
            pxr::VtArray<pxr::GfVec2f> uvs;
            stPrimvar.ComputeFlattened(&uvs); // This resolves the indexing for us

            if (!uvs.empty()) {
                PyObject* uvs_list = PyList_New(uvs.size() * 2);
                for (size_t i = 0; i < uvs.size(); ++i) {
                    PyList_SET_ITEM(uvs_list, i * 2 + 0, PyFloat_FromDouble(uvs[i][0]));
                    PyList_SET_ITEM(uvs_list, i * 2 + 1, PyFloat_FromDouble(uvs[i][1]));
                }
                PyDict_SetItemString(mesh_dict, "uvs", uvs_list);

                std::string interpolation = stPrimvar.GetInterpolation().GetString();
                PyDict_SetItemString(mesh_dict, "uv_interpolation", PyUnicode_FromString(interpolation.c_str()));
            }
        }
        
        // --- Material and Textures ---
        pxr::UsdShadeMaterialBindingAPI bindingAPI(prim);
        pxr::UsdShadeMaterial material = bindingAPI.ComputeBoundMaterial();
        if (material) {
            PyObject* material_dict = PyDict_New();
            // Use the material's path as its unique ID
            PyDict_SetItemString(material_dict, "material_path", PyUnicode_FromString(material.GetPrim().GetPath().GetText()));
            PyDict_SetItemString(material_dict, "name", PyUnicode_FromString(material.GetPrim().GetName().GetText()));

            PyObject* textures_dict = PyDict_New();
            
            // We assume the material contains a UsdPreviewSurface shader
            pxr::UsdShadeShader surfaceShader = material.ComputeSurfaceSource();
            if(surfaceShader) {
                // Iterate over the inputs of the shader to find texture connections
                for (const pxr::UsdShadeInput& input : surfaceShader.GetInputs()) {
                    pxr::UsdShadeConnectableAPI source;
                    pxr::TfToken sourceName;
                    pxr::UsdShadeAttributeType sourceType;
                    if (input.GetConnectedSource(&source, &sourceName, &sourceType)) {
                        // Check if the connected source is a texture shader
                        pxr::UsdShadeShader textureShader(source.GetPrim());
                        if (textureShader) {
                            pxr::UsdShadeInput fileInput = textureShader.GetInput(pxr::TfToken("file"));
                            pxr::SdfAssetPath assetPath;
                            if (fileInput.Get(&assetPath)) {
                                std::string texture_path = assetPath.GetResolvedPath();
                                if (texture_path.empty()){
                                    texture_path = assetPath.GetAssetPath();
                                }
                                PyDict_SetItemString(textures_dict, input.GetBaseName().GetText(), PyUnicode_FromString(texture_path.c_str()));
                            }
                        }
                    }
                }
            }
            PyDict_SetItemString(material_dict, "textures", textures_dict);
            PyDict_SetItemString(mesh_dict, "material", material_dict);
        }

        // Add the dict to our main list
        PyList_Append(meshes_list, mesh_dict);
        Py_DECREF(mesh_dict);
    }

    return meshes_list;
}

// Method definition object
static PyMethodDef RemixNativeMethods[] = {
    {"hello", hello_world, METH_NOARGS, "Prints a hello message from C++ and tests USD."},
    {"import_usd", import_usd, METH_VARARGS, "Imports a USD file and returns mesh data."},
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