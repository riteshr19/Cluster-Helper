/*
 * NUMA utilities C extension for cluster-helper
 * 
 * Provides efficient NUMA node lookup functionality using libnuma.
 */

#include <Python.h>
#include <numa.h>
#include <errno.h>

static PyObject *
numa_utils_get_node_of_cpu(PyObject *self, PyObject *args)
{
    int cpu_id;
    int node_id;
    
    if (!PyArg_ParseTuple(args, "i", &cpu_id))
        return NULL;
    
    /* Check if NUMA is available */
    if (numa_available() < 0) {
        PyErr_SetString(PyExc_RuntimeError, "NUMA not available on this system");
        return NULL;
    }
    
    /* Get NUMA node for the given CPU */
    node_id = numa_node_of_cpu(cpu_id);
    
    if (node_id < 0) {
        PyErr_Format(PyExc_ValueError, "Invalid CPU ID: %d", cpu_id);
        return NULL;
    }
    
    return PyLong_FromLong(node_id);
}

static PyObject *
numa_utils_get_max_node(PyObject *self, PyObject *args)
{
    int max_node;
    
    /* Check if NUMA is available */
    if (numa_available() < 0) {
        PyErr_SetString(PyExc_RuntimeError, "NUMA not available on this system");
        return NULL;
    }
    
    max_node = numa_max_node();
    
    return PyLong_FromLong(max_node);
}

static PyObject *
numa_utils_get_configured_cpus(PyObject *self, PyObject *args)
{
    int max_cpu;
    
    /* Check if NUMA is available */
    if (numa_available() < 0) {
        PyErr_SetString(PyExc_RuntimeError, "NUMA not available on this system");
        return NULL;
    }
    
    max_cpu = numa_num_configured_cpus();
    
    return PyLong_FromLong(max_cpu);
}

static PyObject *
numa_utils_distance(PyObject *self, PyObject *args)
{
    int node1, node2;
    int distance;
    
    if (!PyArg_ParseTuple(args, "ii", &node1, &node2))
        return NULL;
    
    /* Check if NUMA is available */
    if (numa_available() < 0) {
        PyErr_SetString(PyExc_RuntimeError, "NUMA not available on this system");
        return NULL;
    }
    
    distance = numa_distance(node1, node2);
    
    if (distance < 0) {
        PyErr_Format(PyExc_ValueError, "Invalid NUMA nodes: %d, %d", node1, node2);
        return NULL;
    }
    
    return PyLong_FromLong(distance);
}

/* Method definitions */
static PyMethodDef numa_utils_methods[] = {
    {"get_node_of_cpu", numa_utils_get_node_of_cpu, METH_VARARGS,
     "Get NUMA node ID for a given CPU core ID"},
    {"get_max_node", numa_utils_get_max_node, METH_NOARGS,
     "Get the highest NUMA node ID on the system"},
    {"get_configured_cpus", numa_utils_get_configured_cpus, METH_NOARGS,
     "Get the number of configured CPUs on the system"},
    {"distance", numa_utils_distance, METH_VARARGS,
     "Get NUMA distance between two nodes"},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};

/* Module definition */
static struct PyModuleDef numa_utils_module = {
    PyModuleDef_HEAD_INIT,
    "numa_utils",
    "NUMA utilities for cluster-helper",
    -1,
    numa_utils_methods
};

/* Module initialization */
PyMODINIT_FUNC
PyInit_numa_utils(void)
{
    return PyModule_Create(&numa_utils_module);
}