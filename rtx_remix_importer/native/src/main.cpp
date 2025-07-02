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
#include <string>
#include <thread>
#include <algorithm>
#include <cstdlib> // For system()
#include <unistd.h> // for access()
#include <pxr/usd/sdf/layer.h>          // For SdfLayer
#include <pxr/usd/sdf/primSpec.h>       // For SdfPrimSpec
#include <pxr/usd/sdf/attributeSpec.h>  // For SdfAttributeSpec
#include <pxr/base/vt/value.h>          // For VtValue

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

// Forward declaration of helper functions
PyObject* extract_mesh_data(const pxr::UsdPrim& prim);
PyObject* extract_material_data(const pxr::UsdShadeMaterial& material);

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
    
    PyObject* meshes_list = PyList_New(0);
    for (const auto& prim : stage->TraverseAll()) {
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

        PyObject* mesh_data = extract_mesh_data(prim);
        if (mesh_data) {
            PyList_Append(meshes_list, mesh_data);
            Py_DECREF(mesh_data);
        }
    }
    return meshes_list;
}

// --- Helper for C++ Texture Conversion ---
bool run_texconv_on_file(const std::string& texconv_path, const std::string& dds_path, const std::string& cache_dir) {
    std::string command;
    #ifdef _WIN32
        command = "\"" + texconv_path + "\" -ft png -o \"" + cache_dir + "\" -y \"" + dds_path + "\"";
    #else
        if (system("which wine > /dev/null 2>&1") != 0) {
            std::cerr << "ERROR: wine is not installed or not in PATH." << std::endl;
            return false;
        }
        
        // Convert Linux paths to Wine paths (e.g., /path/to/file -> Z:\path\to\file)
        std::string wine_texconv_path = "Z:" + texconv_path;
        std::string wine_dds_path = "Z:" + dds_path;
        std::string wine_cache_dir = "Z:" + cache_dir;
        
        // Replace all forward slashes with backslashes for Windows compatibility
        std::replace(wine_texconv_path.begin(), wine_texconv_path.end(), '/', '\\');
        std::replace(wine_dds_path.begin(), wine_dds_path.end(), '/', '\\');
        std::replace(wine_cache_dir.begin(), wine_cache_dir.end(), '/', '\\');

        command = "wine \"" + wine_texconv_path + "\" -ft png -o \"" + wine_cache_dir + "\" -y \"" + wine_dds_path + "\"";
    #endif
    
    std::cout << "  > Running: " << command << std::endl;
    int result = system(command.c_str());
    
    if (result != 0) {
        std::cerr << "  > ERROR: texconv command failed for " << dds_path << std::endl;
        return false;
    }
    return true;
}

// --- New Native Function: Batch Texture Conversion ---
static PyObject* batch_convert_textures_native(PyObject* self, PyObject* args) {
    PyObject* dds_paths_list;
    const char* texconv_path_char;
    const char* cache_dir_char;

    if (!PyArg_ParseTuple(args, "O!ss", &PyList_Type, &dds_paths_list, &texconv_path_char, &cache_dir_char)) {
        return NULL;
    }

    std::string texconv_path(texconv_path_char);
    std::string cache_dir(cache_dir_char);
    std::vector<std::string> dds_paths;
    for (Py_ssize_t i = 0; i < PyList_Size(dds_paths_list); ++i) {
        PyObject* item = PyList_GetItem(dds_paths_list, i);
        dds_paths.push_back(PyUnicode_AsUTF8(item));
    }

    std::cout << "--- Starting Native Texture Conversion (" << dds_paths.size() << " files) ---" << std::endl;

    // --- Parallel Execution ---
    unsigned int num_threads = std::thread::hardware_concurrency() * 4;
    std::vector<std::thread> threads;
    unsigned int files_per_thread = dds_paths.size() / num_threads;
    if (files_per_thread == 0) files_per_thread = 1;

    for (unsigned int i = 0; i < num_threads && i * files_per_thread < dds_paths.size(); ++i) {
        threads.emplace_back([=] {
            unsigned int start = i * files_per_thread;
            unsigned int end = start + files_per_thread;
            if (end > dds_paths.size()) end = dds_paths.size();
            
            for (unsigned int j = start; j < end; ++j) {
                run_texconv_on_file(texconv_path, dds_paths[j], cache_dir);
            }
        });
    }

    for (auto& t : threads) {
        if (t.joinable()) {
            t.join();
        }
    }

    std::cout << "--- Native Texture Conversion Finished ---" << std::endl;
    Py_RETURN_NONE;
}

// --- New Native Function: Apply Mod File ---
static PyObject* apply_mod_native(PyObject* self, PyObject* args) {
    const char* original_usd_path;
    const char* mod_usd_path;

    if (!PyArg_ParseTuple(args, "ss", &original_usd_path, &mod_usd_path)) {
        return NULL;
    }

    std::cout << "--- Applying Mod File ---" << std::endl;
    std::cout << "  > Base Capture: " << original_usd_path << std::endl;
    std::cout << "  > Mod File: " << mod_usd_path << std::endl;

    // Open the original stage
    pxr::UsdStageRefPtr stage = pxr::UsdStage::Open(original_usd_path);
    if (!stage) {
        PyErr_SetString(PyExc_IOError, "Failed to open original USD stage.");
        return NULL;
    }

    // Apply the mod file as a stronger sublayer
    stage->GetRootLayer()->InsertSubLayerPath(mod_usd_path);

    PyObject* replacements_dict = PyDict_New();
    pxr::SdfLayerHandle modLayer = pxr::SdfLayer::FindOrOpen(mod_usd_path);

    if (modLayer) {
        // We need to traverse the composed stage to get the final, overridden prims
        for(const pxr::UsdPrim& prim : stage->TraverseAll()) {
            // Check if this prim has opinions authored in the mod layer
            bool defined_in_mod = false;
            for (const auto& spec : prim.GetPrimStack()) {
                if (spec->GetLayer() == modLayer) {
                    defined_in_mod = true;
                    break;
                }
            }

            if (defined_in_mod) {
                std::cout << "  > Found overridden prim: " << prim.GetPath() << std::endl;
                
                // Now, extract its data just like we do in the main import function
                if (prim.IsA<pxr::UsdGeomMesh>()) {
                    // This is a mesh replacement
                    PyObject* mesh_data = extract_mesh_data(prim); // Re-use our existing mesh extraction logic
                    if (mesh_data) {
                        PyDict_SetItemString(replacements_dict, prim.GetPath().GetText(), mesh_data);
                    }
                } else if (prim.IsA<pxr::UsdShadeMaterial>()) {
                    // This is a material replacement
                    PyObject* material_data = extract_material_data(pxr::UsdShadeMaterial(prim)); // New helper function
                    if (material_data) {
                        PyDict_SetItemString(replacements_dict, prim.GetPath().GetText(), material_data);
                    }
                }
            }
        }
    }

    std::cout << "--- Mod File Applied ---" << std::endl;
    return replacements_dict;
}

// Helper function to extract mesh data (now with full logic)
PyObject* extract_mesh_data(const pxr::UsdPrim& prim) {
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

    // --- Name ---
    std::string instance_name;
    std::string prim_name = prim.GetName().GetText();
    // If a prim is just called "mesh", its parent usually has the meaningful instance name.
    if (prim_name == "mesh" && prim.GetParent()) {
        instance_name = prim.GetParent().GetName().GetText();
    } else {
        instance_name = prim_name;
    }
    PyDict_SetItemString(mesh_dict, "instance_name", PyUnicode_FromString(instance_name.c_str()));
    
    // --- Paths ---
    // The path to the master/prototype. This is the key for sharing mesh data.
    pxr::UsdPrim prototype = prim.GetPrototype();
    std::string mesh_definition_path = prototype ? prototype.GetPath().GetText() : prim.GetPath().GetText();
    PyDict_SetItemString(mesh_dict, "mesh_definition_path", PyUnicode_FromString(mesh_definition_path.c_str()));

    // The path to the unique instance itself.
    PyDict_SetItemString(mesh_dict, "instance_path", PyUnicode_FromString(prim.GetPath().GetText()));

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
        PyObject* material_data = extract_material_data(material);
        if (material_data) {
            PyDict_SetItemString(mesh_dict, "material", material_data);
            // Py_DECREF(material_data) is handled by the caller
        }
    }
    
    return mesh_dict;
}

// Helper function to extract material data (now with full logic)
PyObject* extract_material_data(const pxr::UsdShadeMaterial& material) {
    PyObject* material_dict = PyDict_New();
    // Use the material's path as its unique ID
    PyDict_SetItemString(material_dict, "material_path", PyUnicode_FromString(material.GetPrim().GetPath().GetText()));
    PyDict_SetItemString(material_dict, "name", PyUnicode_FromString(material.GetPrim().GetName().GetText()));

    PyObject* textures_dict = PyDict_New();
    
    // Explicitly look for the MDL surface output, which is more reliable for Remix captures
    pxr::UsdShadeOutput surfaceOutput = material.GetOutput(pxr::TfToken("mdl:surface"));
    if (surfaceOutput) {
        pxr::UsdShadeConnectableAPI source;
        pxr::TfToken sourceName;
        pxr::UsdShadeAttributeType sourceType;

        if (surfaceOutput.GetConnectedSource(&source, &sourceName, &sourceType)) {
            pxr::UsdShadeShader surfaceShader(source.GetPrim());
            if (surfaceShader) {
                 std::cout << "    > Found surface shader via mdl:surface: " << surfaceShader.GetPrim().GetPath() << std::endl;
                // Iterate over the inputs of the shader to find texture connections
                for (const pxr::UsdShadeInput& input : surfaceShader.GetInputs()) {
                    std::cout << "      - Checking input: " << input.GetBaseName() << std::endl;
                    
                    pxr::SdfAssetPath assetPath;
                    bool found_texture = false;

                    // First, try to see if the input is connected to a texture shader
                    pxr::UsdShadeConnectableAPI textureSource;
                    pxr::TfToken textureSourceName;
                    pxr::UsdShadeAttributeType textureSourceType;
                    if (input.GetConnectedSource(&textureSource, &textureSourceName, &textureSourceType)) {
                        pxr::UsdShadeShader textureShader(textureSource.GetPrim());
                        if (textureShader) {
                            std::cout << "        > Connected to texture shader: " << textureShader.GetPrim().GetPath() << std::endl;
                            pxr::UsdShadeInput fileInput = textureShader.GetInput(pxr::TfToken("file"));
                            if (fileInput && fileInput.Get(&assetPath)) {
                                found_texture = true;
                            }
                        }
                    }
                    
                    // If not connected, check if the input itself holds the texture path
                    if (!found_texture) {
                        if (input.Get(&assetPath)) {
                            std::cout << "        > Found asset path directly on input." << std::endl;
                            found_texture = true;
                        }
                    }

                    if (found_texture) {
                        std::cout << "          > Found asset path: " << assetPath.GetAssetPath() << std::endl;
                        std::string texture_path = assetPath.GetResolvedPath();
                        if (texture_path.empty()){
                            texture_path = assetPath.GetAssetPath();
                        }
                        std::cout << "          > Resolved texture path: " << texture_path << std::endl;
                        PyDict_SetItemString(textures_dict, input.GetBaseName().GetText(), PyUnicode_FromString(texture_path.c_str()));
                    }
                }
            }
        }
    } else {
        std::cout << "    > No 'mdl:surface' output found for material." << std::endl;
    }
    PyDict_SetItemString(material_dict, "textures", textures_dict);
    return material_dict;
}

// --- New Native Function: Update Prim Transform ---
static PyObject* update_prim_transform(PyObject* self, PyObject* args) {
    const char* mod_file_path;
    const char* prim_path;
    PyObject* transform_matrix_list;

    if (!PyArg_ParseTuple(args, "ssO!", &mod_file_path, &prim_path, &PyList_Type, &transform_matrix_list)) {
        return NULL;
    }

    pxr::SdfLayerRefPtr modLayer = pxr::SdfLayer::FindOrOpen(mod_file_path);
    if (!modLayer) {
        PyErr_SetString(PyExc_IOError, "Failed to open mod layer for writing.");
        return NULL;
    }

    // Create a prim spec for the override. This will create the hierarchy if it doesn't exist.
    pxr::SdfPrimSpecHandle primSpec = pxr::SdfCreatePrimInLayer(modLayer, pxr::SdfPath(prim_path));
    
    // Convert Python list to GfMatrix4d
    pxr::GfMatrix4d transform_matrix;
    double* matrix_data = transform_matrix.GetArray();
    for (int i = 0; i < 16; ++i) {
        matrix_data[i] = PyFloat_AsDouble(PyList_GetItem(transform_matrix_list, i));
    }

    // Author the transform operation by setting the 'xformOp:transform' attribute
    primSpec->SetInfo(pxr::TfToken("typeName"), pxr::VtValue("Xform"));
    pxr::SdfAttributeSpecHandle xformOp = pxr::SdfAttributeSpec::New(primSpec, "xformOp:transform", pxr::SdfValueTypeNames->Matrix4d);
    xformOp->SetDefaultValue(pxr::VtValue(transform_matrix));
    
    // Save the changes
    modLayer->Save();
    
    std::cout << "  > Wrote transform for: " << prim_path << std::endl;

    Py_RETURN_NONE;
}

// Method definition object
static PyMethodDef RemixNativeMethods[] = {
    {"hello", hello_world, METH_NOARGS, "Prints a hello message from C++ and tests USD."},
    {"import_usd", import_usd, METH_VARARGS, "Imports a USD file and returns mesh data."},
    {"batch_convert_textures", batch_convert_textures_native, METH_VARARGS, "Converts a list of DDS files to PNG in parallel."},
    {"apply_mod", apply_mod_native, METH_VARARGS, "Applies a mod.usda file to an existing stage."},
    {"update_prim_transform", update_prim_transform, METH_VARARGS, "Updates the transform of a prim in the mod file."},
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