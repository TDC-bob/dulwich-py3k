/* 
 * Copyright (C) 2009 Jelmer Vernooij <jelmer@samba.org>
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; version 2
 * of the License or (at your option) a later version.
 * 
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 * 
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
 * MA  02110-1301, USA.
 */

#include <Python.h>
#include <stdint.h>
#include <string.h>

static int py_is_sha(PyObject *sha)
{
	if (!PyBytes_CheckExact(sha))
		return 0;

	if (PyBytes_GET_SIZE(sha) != 20)
		return 0;

	return 1;
}

static size_t get_delta_header_size(uint8_t *delta, int *index, int length)
{
	size_t size = 0;
	int i = 0;
	while ((*index) < length) {
		uint8_t cmd = delta[*index];
		(*index)++;
		size |= (cmd & ~0x80) << i;
		i += 7;
		if (!(cmd & 0x80))
			break;
	}
	return size;
}

static PyObject *py_chunked_as_bytes(PyObject *py_buf)
{
	if (PyList_Check(py_buf)) {
		PyObject *sep = PyBytes_FromStringAndSize("", 0);
		if (sep == NULL) {
			PyErr_NoMemory();
			return NULL;
		}
		py_buf = _PyBytes_Join(sep, py_buf);
		Py_DECREF(sep);
		if (py_buf == NULL) {
			PyErr_NoMemory();
			return NULL;
		}
	} else if (PyBytes_Check(py_buf)) {
		Py_INCREF(py_buf);
	} else {
		PyErr_SetString(PyExc_TypeError,
			"src_buf is not a bytes chunk or a list of bytes chunks");
		return NULL;
	}
	return py_buf;
}

static PyObject *py_apply_delta(PyObject *self, PyObject *args)
{
	uint8_t *src_buf, *delta;
	int src_buf_len, delta_len;
	size_t src_size, dest_size;
	size_t outindex = 0;
	int index;
	uint8_t *out;
	PyObject *ret, *py_src_buf, *py_delta;

	if (!PyArg_ParseTuple(args, "OO", &py_src_buf, &py_delta))
		return NULL;

	py_src_buf = py_chunked_as_bytes(py_src_buf);
	if (py_src_buf == NULL)
		return NULL;

	py_delta = py_chunked_as_bytes(py_delta);
	if (py_delta == NULL) {
		Py_DECREF(py_src_buf);
		return NULL;
	}

	src_buf = (uint8_t *)PyBytes_AS_STRING(py_src_buf);
	src_buf_len = PyBytes_GET_SIZE(py_src_buf);

	delta = (uint8_t *)PyBytes_AS_STRING(py_delta);
	delta_len = PyBytes_GET_SIZE(py_delta);

	index = 0;
	src_size = get_delta_header_size(delta, &index, delta_len);
	if (src_size != src_buf_len) {
		PyErr_Format(PyExc_ValueError, 
			"Unexpected source buffer size: %lu vs %d", src_size, src_buf_len);
		Py_DECREF(py_src_buf);
		Py_DECREF(py_delta);
		return NULL;
	}
	dest_size = get_delta_header_size(delta, &index, delta_len);

	ret = PyBytes_FromStringAndSize(NULL, dest_size);
	if (ret == NULL) {
		PyErr_NoMemory();
		Py_DECREF(py_src_buf);
		Py_DECREF(py_delta);
		return NULL;
	}
	out = (uint8_t *)PyBytes_AS_STRING(ret);

	while (index < delta_len) {
		char cmd = delta[index];
		index++;
		if (cmd & 0x80) {
			size_t cp_off = 0, cp_size = 0;
			int i;
			for (i = 0; i < 4; i++) {
				if (cmd & (1 << i)) {
					uint8_t x = delta[index];
					index++;
					cp_off |= x << (i * 8);
				}
			}
			for (i = 0; i < 3; i++) {
				if (cmd & (1 << (4+i))) {
					uint8_t x = delta[index];
					index++;
					cp_size |= x << (i * 8);
				}
			}
			if (cp_size == 0)
				cp_size = 0x10000;
			if (cp_off + cp_size < cp_size ||
				cp_off + cp_size > src_size ||
				cp_size > dest_size)
				break;
			memcpy(out+outindex, src_buf+cp_off, cp_size);
			outindex += cp_size;
		} else if (cmd != 0) {
			memcpy(out+outindex, delta+index, cmd);
			outindex += cmd;
			index += cmd;
		} else {
			PyErr_SetString(PyExc_ValueError, "Invalid opcode 0");
			Py_DECREF(ret);
			Py_DECREF(py_delta);
			Py_DECREF(py_src_buf);
			return NULL;
		}
	}
	Py_DECREF(py_src_buf);
	Py_DECREF(py_delta);

	if (index != delta_len) {
		PyErr_SetString(PyExc_ValueError, "delta not empty");
		Py_DECREF(ret);
		return NULL;
	}

	if (dest_size != outindex) {
		PyErr_SetString(PyExc_ValueError, "dest size incorrect");
		Py_DECREF(ret);
		return NULL;
	}

	return Py_BuildValue("[N]", ret);
}

static PyObject *py_bisect_find_sha(PyObject *self, PyObject *args)
{
	PyObject *unpack_name;
	Py_buffer sha_buf;
	long start, end;
	if (!PyArg_ParseTuple(args, "lly*O", &start, &end, 
						  &sha_buf, &unpack_name))
		return NULL;

	if (sha_buf.len != 20) {
		PyErr_SetString(PyExc_ValueError, "Sha is not 20 bytes long");
		PyBuffer_Release(&sha_buf);
		return NULL;
	}
	if (start > end) {
		PyErr_SetString(PyExc_AssertionError, "start > end");
		PyBuffer_Release(&sha_buf);
		return NULL;
	}

	while (start <= end) {
		PyObject *file_sha;
		long i = (start + end) / 2;
		int cmp;
		file_sha = PyObject_CallFunction(unpack_name, "l", i);
		if (file_sha == NULL) {
			PyBuffer_Release(&sha_buf);
			return NULL;
		}
		if (!py_is_sha(file_sha)) {
			PyErr_SetString(PyExc_TypeError, "unpack_name returned non-sha object");
			Py_DECREF(file_sha);
			PyBuffer_Release(&sha_buf);
			return NULL;
		}

		cmp = memcmp(PyBytes_AS_STRING(file_sha), sha_buf.buf, 20);

		Py_DECREF(file_sha);
		if (cmp < 0)
			start = i + 1;
		else if (cmp > 0)
			end = i - 1;
		else {
			return PyLong_FromLong(i);
		}
	}
	PyBuffer_Release(&sha_buf);
	Py_RETURN_NONE;
}


static PyMethodDef py_pack_methods[] = {
	{ "apply_delta", (PyCFunction)py_apply_delta, METH_VARARGS, NULL },
	{ "bisect_find_sha", (PyCFunction)py_bisect_find_sha, METH_VARARGS, NULL },
	{ NULL, NULL, 0, NULL }
};

static struct PyModuleDef py_packmodule = {
	PyModuleDef_HEAD_INIT,
	"_pack",    /* name of module */
	NULL,       /* module documentation, may be NULL */
	-1,         /* size of per-interpreter state of the module,
	               or -1 if the module keeps state in global variables. */
	py_pack_methods
};

PyObject *PyInit__pack(void)
{
	return PyModule_Create(&py_packmodule);
}
