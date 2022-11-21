"""
(C) Copyright 2018-2022 Intel Corporation.

SPDX-License-Identifier: BSD-2-Clause-Patent
"""

import os
from os.path import join
import re
import ctypes

from pydaos.raw import str_to_c_uuid, DaosContainer, DaosObj, IORequest

from exception_utils import CommandFailure
from test_utils_container import TestContainer
from ior_test_base import IorTestBase
from mdtest_test_base import MdtestBase
from data_mover_utils import DcpCommand, DsyncCommand
from data_mover_utils import DserializeCommand, DdeserializeCommand
from data_mover_utils import uuid_from_obj
from duns_utils import format_path
from general_utils import create_string_buffer
from command_utils_base import BasicParameter
from test_utils_base import LabelGenerator


class DataMoverTestBase(IorTestBase, MdtestBase):
    # pylint: disable=too-many-ancestors
    """Base DataMover test class.

    Optional yaml config values:
        datamover/posix_root (str): path to POSIX filesystem.
        datamover/tool (list): default datamover tool to use.
        datamover/np (int): default processes for all tools.
        datamover/ppn (int): default processes-per-client for all tools.

    Sample Use Case:
        # Create test file
        run_ior_with_params("DFS", "/testFile, pool1, cont1, flags="-w -K")

        # Set dcp as the tool to use
        self.set_tool("DCP")

        # Copy from DAOS to POSIX
        run_datamover(
            "some test description",
            src="daos://pool1/cont1/testFile",
            dst="/some/posix/path/testFile")

        # Verify destination file
        run_ior_with_params("POSIX", "/some/posix/path/testFile", flags="-r -R")
    :avocado: recursive

    """

    # The valid datamover tools that can be used
    TOOLS = (
        "DCP",        # mpifileutils dcp
        "DSYNC",      # mpifileutils dsync
        "DSERIAL",    # mpifileutils daos-serialize + daos-deserialize
        "FS_COPY",    # daos filesystem copy
        "CONT_CLONE"  # daos container clone
    )

    def __init__(self, *args, **kwargs):
        """Initialize a DataMoverTestBase object."""
        super().__init__(*args, **kwargs)
        self.tool = None
        self.api = None
        self.daos_cmd = None
        self.pool = []

        # Override processes and np from IorTestBase and MdtestBase to use "datamover" namespace.
        # Define processes and np for each datamover tool, which defaults to the "datamover" one.
        self.processes = None
        self.ppn = None
        self.datamover_np = None
        self.datamover_ppn = None
        self.ior_np = None
        self.ior_ppn = None
        self.mdtest_np = None
        self.mdtest_ppn = None
        self.dcp_np = None
        self.dsync_np = None
        self.dserialize_np = None
        self.ddeserialize_np = None

        # Root directory for POSIX paths. Default is self.tmp
        posix_root_map = {'self.workdir': self.workdir, 'self.tmp': self.tmp}
        self.posix_root = BasicParameter(None, mapped_values=posix_root_map)

        # List of local test paths to create and remove
        self.posix_local_test_paths = []

        # List of shared test paths to create and remove
        self.posix_shared_test_paths = []

        # paths to unmount in teardown
        self.mounted_posix_test_paths = []

        self._test_path_generator = LabelGenerator()

    def setUp(self):
        """Set up each test case."""
        # Start the servers and agents
        super().setUp()

        # initialize daos_cmd
        self.daos_cmd = self.get_daos_command()

        # Get the processes and np for all datamover tools, as well as for individual tools.
        self.datamover_np = self.params.get("np", '/run/datamover/*', 1)
        self.datamover_ppn = self.params.get("ppn", '/run/datamover/*', 1)
        self.ior_np = self.params.get("np", '/run/ior/client_processes/*', 1)
        self.ior_ppn = self.params.get("ppn", '/run/ior/client_processes/*', None)
        self.mdtest_np = self.params.get("np", '/run/mdtest/client_processes/*', 1)
        self.mdtest_ppn = self.params.get("ppn", '/run/mdtest/client_processes/*', None)
        self.dcp_np = self.params.get("np", "/run/dcp/*", self.datamover_np)
        self.dcp_ppn = self.params.get("ppn", "/run/dcp/*", self.datamover_ppn)
        self.dsync_np = self.params.get("np", "/run/dsync/*", self.datamover_np)
        self.dsync_ppn = self.params.get("ppn", "/run/dsync/*", self.datamover_ppn)
        self.dserialize_np = self.params.get("np", "/run/dserialize/*", self.datamover_np)
        self.dserialize_ppn = self.params.get("ppn", "/run/dserialize/*", self.datamover_ppn)
        self.ddeserialize_np = self.params.get("np", "/run/ddeserialize/*", self.datamover_np)
        self.ddeserialize_ppn = self.params.get("ppn", "/run/ddeserialize/*", self.datamover_ppn)

        self.posix_root.update_default(self.tmp)
        self.posix_root.get_yaml_value("posix_root", self, "/run/datamover/*")

        tool = self.params.get("tool", "/run/datamover/*")
        if tool:
            self.set_tool(tool)

    def pre_tear_down(self):
        """Tear down steps to run before tearDown().

        Returns:
            list: a list of error strings to report at the end of tearDown().

        """
        # doesn't append to error list because it reports an error if all
        # processes completed successfully (nothing to stop), but this call is
        # necessary in the case that mpi processes are ran across multiple nodes
        # and a timeout occurs. If this happens then cleanup on shared posix
        # directories causes errors (because an MPI process might still have it open)
        error_list = []

        # cleanup mounted paths
        if self.mounted_posix_test_paths:
            path_list = self._get_posix_test_path_list(path_list=self.mounted_posix_test_paths)
            for item in path_list:
                # need to remove contents before umount
                rm_cmd = "rm -rf {}/*".format(item)
                try:
                    self._execute_command(rm_cmd)
                except CommandFailure as error:
                    error_list.append("Error removing directory contents: {}".format(error))
                umount_cmd = "sudo umount -f {}".format(item)
                try:
                    self._execute_command(umount_cmd)
                except CommandFailure as error:
                    error_list.append("Error umounting posix test directory: {}".format(error))

        # cleanup local paths
        if self.posix_local_test_paths:
            command = "rm -rf {}".format(self._get_posix_test_path_string())
            try:
                self._execute_command(command)
            except CommandFailure as error:
                error_list.append("Error removing created directories: {}".format(error))

        # cleanup shared paths (only runs on one node in job)
        if self.posix_shared_test_paths:
            shared_path_strs = self._get_posix_test_path_string(path=self.posix_shared_test_paths)
            command = "rm -rf {}".format(shared_path_strs)
            try:
                # only call rm on one client since this is cleaning up shared dir
                self._execute_command(command, hosts=list(self.hostlist_clients)[0:1])
            except CommandFailure as error:
                error_list.append("Error removing created directories: {}".format(error))
        return error_list

    def set_api(self, api):
        """Set the api.

        Args:
            api (str): the api to use.

        """
        self.api = api

    def set_tool(self, tool):
        """Set the copy tool.

        Converts to upper-case and fails if the tool is not valid.

        Args:
            tool (str): the tool to use. Must be in self.TOOLS

        """
        _tool = str(tool).upper()
        if _tool in self.TOOLS:
            self.log.info("DataMover tool = %s", _tool)
            self.tool = _tool
        else:
            self.fail("Invalid tool: {}".format(_tool))

    def _get_posix_test_path_list(self, path_list=None):
        """Get a list of quoted posix test path strings.

        Returns:
            list: a list of quoted posix test path strings

        """
        path_list = path_list or self.posix_local_test_paths

        return ["'{}'".format(item) for item in path_list]

    def _get_posix_test_path_string(self, path=None):
        """Get a string of all of the quoted posix test path strings.

        Returns:
            str: a string of all of the quoted posix test path strings

        """
        return " ".join(self._get_posix_test_path_list(path_list=path))

    def new_posix_test_path(self, shared=False, create=True, parent=None, mount_dir=False):
        """Generate a new, unique posix path.

        Args:
            shared (bool): Whether to create a directory shared across nodes or local.
                Defaults to False.
            create (bool): Whether to create the directory.
                Defaults to True.
            mount_dir (bool): Whether or not posix directory will be manually mounted in tmpfs.
            parent (str, optional): The parent directory to create the
                path in. Defaults to self.posix_root, which defaults to self.tmp.

        Returns:
            str: the posix path.

        """
        # make directory name unique to datamover test
        method = self.get_test_name()
        dir_name = self._test_path_generator.get_label(method)
        path = join(parent or self.posix_root.value, dir_name)

        # Add to the list of posix paths
        if shared:
            self.posix_shared_test_paths.append(path)
        else:
            self.posix_local_test_paths.append(path)

        if create:
            # Create the directory
            self.execute_cmd("mkdir -p '{}'".format(path))

        # mount small tmpfs filesystem on posix path, using size required sudo
        # add mount_dir to mounted list for use when umounting
        if mount_dir:
            self.mounted_posix_test_paths.append(path)
            self.execute_cmd("sudo mount -t tmpfs none '{}' -o size=128M".format(path))

        return path

    def new_daos_test_path(self, parent="/"):
        """Get a new, unique daos container path.

        Args:
            parent (str, optional): parent directory relative to container root. Defaults to "/"

        Returns:
            str: the path relative to parent.

        """
        return join(parent, self._test_path_generator.get_label('daos_test'))

    def create_pool(self, **params):
        """Create a TestPool object and adds to self.pool.

        Returns:
            TestPool: the created pool

        """
        pool = self.get_pool(connect=False, **params)

        # Save the pool
        self.pool.append(pool)

        return pool

    def get_cont(self, pool, cont):
        """Get an existing container.

        Args:
            pool (TestPool): pool to open the container in.
            cont (str): container uuid or label.

        Returns:
            TestContainer: the container object

        """
        # Query the container for existence and to get the uuid from a label
        query_response = self.daos_cmd.container_query(pool=pool.uuid, cont=cont)['response']
        cont_uuid = query_response['container_uuid']

        # Convert default label to None
        cont_label = query_response['container_label']
        if cont_label == 'container_label_not_set':
            cont_label = None

        # Create a TestContainer and DaosContainer instance
        container = TestContainer(pool, daos_command=self.daos_cmd)
        container.container = DaosContainer(pool.context)
        container.container.uuid = str_to_c_uuid(cont_uuid)
        container.container.poh = pool.pool.handle
        container.uuid = container.container.get_uuid_str()
        container.label.value = cont_label

        return container

    def parse_create_cont_label(self, output):
        """Parse a uuid or label from create container output.

        Format:
            Successfully created container (.*)

        Args:
            output (str): The string to parse for the uuid or label

        Returns:
            str: The parsed uuid or label

        """
        label_search = re.search(r"Successfully created container (.*)", output)
        if not label_search:
            self.fail("Failed to parse container label")
        return label_search.group(1).strip()

    def dataset_gen(self, cont, num_objs, num_dkeys, num_akeys_single,
                    num_akeys_array, akey_sizes, akey_extents):
        """Generate a dataset with some number of objects, dkeys, and akeys.

        Expects the container to be created with the API control method.

        Args:
            cont (TestContainer): the container.
            num_objs (int): number of objects to create in the container.
            num_dkeys (int): number of dkeys to create per object.
            num_akeys_single (int): number of DAOS_IOD_SINGLE akeys per dkey.
            num_akeys_array (int): number of DAOS_IOD_ARRAY akeys per dkey.
            akey_sizes (list): varying akey sizes to iterate.
            akey_extents (list): varying number of akey extents to iterate.

        Returns:
            list: a list of DaosObj created.

        """
        self.log.info("Creating dataset in %s/%s",
                      str(cont.pool.uuid), str(cont.uuid))

        cont.open()

        obj_list = []

        for obj_idx in range(num_objs):
            # Open the obj
            obj = DaosObj(cont.pool.context, cont.container)
            obj_list.append(obj)
            obj.create(rank=obj_idx, objcls=3)
            obj.open()

            ioreq = IORequest(cont.pool.context, cont.container, obj)
            for dkey_idx in range(num_dkeys):
                dkey = "dkey {}".format(dkey_idx)
                c_dkey = create_string_buffer(dkey)

                for akey_idx in range(num_akeys_single):
                    # Round-robin to get the size of data and
                    # arbitrarily use a number 0-9 to fill data
                    akey_size_idx = akey_idx % len(akey_sizes)
                    data_size = akey_sizes[akey_size_idx]
                    data_val = str(akey_idx % 10)
                    data = data_size * data_val
                    akey = "akey single {}".format(akey_idx)
                    c_akey = create_string_buffer(akey)
                    c_data = create_string_buffer(data)
                    c_size = ctypes.c_size_t(ctypes.sizeof(c_data))
                    ioreq.single_insert(c_dkey, c_akey, c_data, c_size)

                for akey_idx in range(num_akeys_array):
                    # Round-robin to get the size of data and
                    # the number of extents, and
                    # arbitrarily use a number 0-9 to fill data
                    akey_size_idx = akey_idx % len(akey_sizes)
                    data_size = akey_sizes[akey_size_idx]
                    akey_extent_idx = akey_idx % len(akey_extents)
                    num_extents = akey_extents[akey_extent_idx]
                    akey = "akey array {}".format(akey_idx)
                    c_akey = create_string_buffer(akey)
                    c_data = []
                    for data_idx in range(num_extents):
                        data_val = str(data_idx % 10)
                        data = data_size * data_val
                        c_data.append([create_string_buffer(data), data_size])
                    ioreq.insert_array(c_dkey, c_akey, c_data)

            obj.close()
        cont.close()

        return obj_list

    # pylint: disable=too-many-locals
    def dataset_verify(self, obj_list, cont, num_objs, num_dkeys,
                       num_akeys_single, num_akeys_array, akey_sizes,
                       akey_extents):
        """Verify a dataset generated with dataset_gen.

        Args:
            obj_list (list): obj_list returned from dataset_gen.
            cont (TestContainer): the container.
            num_objs (int): number of objects created in the container.
            num_dkeys (int): number of dkeys created per object.
            num_akeys_single (int): number of DAOS_IOD_SINGLE akeys per dkey.
            num_akeys_array (int): number of DAOS_IOD_ARRAY akeys per dkey.
            akey_sizes (list): varying akey sizes to iterate.
            akey_extents (list): varying number of akey extents to iterate.

        """
        self.log.info("Verifying dataset in %s/%s",
                      str(cont.pool.uuid), str(cont.uuid))

        cont.open()

        for obj_idx in range(num_objs):
            # Open the obj
            c_oid = obj_list[obj_idx].c_oid
            obj = DaosObj(cont.pool.context, cont.container, c_oid=c_oid)
            obj.open()

            ioreq = IORequest(cont.pool.context, cont.container, obj)
            for dkey_idx in range(num_dkeys):
                dkey = "dkey {}".format(dkey_idx)
                c_dkey = create_string_buffer(dkey)

                for akey_idx in range(num_akeys_single):
                    # Round-robin to get the size of data and
                    # arbitrarily use a number 0-9 to fill data
                    akey_size_idx = akey_idx % len(akey_sizes)
                    data_size = akey_sizes[akey_size_idx]
                    data_val = str(akey_idx % 10)
                    data = data_size * data_val
                    akey = "akey single {}".format(akey_idx)
                    c_akey = create_string_buffer(akey)
                    c_data = ioreq.single_fetch(c_dkey, c_akey, data_size + 1)
                    actual_data = str(c_data.value.decode())
                    if actual_data != data:
                        self.log.info("Expected:\n%s\nBut got:\n%s",
                                      data[:100] + "...",
                                      actual_data[:100] + "...")
                        self.log.info(
                            "For:\nobj: %s.%s\ndkey: %s\nakey: %s",
                            str(obj.c_oid.hi), str(obj.c_oid.lo),
                            dkey, akey)
                        self.fail("Single value verification failed.")

                for akey_idx in range(num_akeys_array):
                    # Round-robin to get the size of data and
                    # the number of extents, and
                    # arbitrarily use a number 0-9 to fill data
                    akey_size_idx = akey_idx % len(akey_sizes)
                    data_size = akey_sizes[akey_size_idx]
                    akey_extent_idx = akey_idx % len(akey_extents)
                    num_extents = akey_extents[akey_extent_idx]
                    akey = "akey array {}".format(akey_idx)
                    c_akey = create_string_buffer(akey)
                    c_num_extents = ctypes.c_uint(num_extents)
                    c_data_size = ctypes.c_size_t(data_size)
                    actual_data = ioreq.fetch_array(c_dkey, c_akey, c_num_extents, c_data_size)
                    for data_idx in range(num_extents):
                        data_val = str(data_idx % 10)
                        data = data_size * data_val
                        actual_idx = str(actual_data[data_idx].decode())
                        if data != actual_idx:
                            self.log.info(
                                "Expected:\n%s\nBut got:\n%s",
                                data[:100] + "...",
                                actual_idx + "...")
                            self.log.info(
                                "For:\nobj: %s.%s\ndkey: %s\nakey: %s",
                                str(obj.c_oid.hi), str(obj.c_oid.lo),
                                dkey, akey)
                            self.fail("Array verification failed.")

            obj.close()
        cont.close()

    def _get_dcp_cmd(self, **params):
        """Get a new DcpCommand object."""
        dcp_cmd = DcpCommand(self.hostlist_clients, self.workdir)
        dcp_cmd.get_params(self)

        if self.api and 'daos_api' not in params:
            params['daos_api'] = self.api

        dcp_cmd.update_params(**params)
        return dcp_cmd

    def _get_dsync_cmd(self, **params):
        """Get a new DsyncCommand object."""
        dsync_cmd = DsyncCommand(self.hostlist_clients, self.workdir)
        dsync_cmd.get_params(self)

        if self.api and 'daos_api' not in params:
            params['daos_api'] = self.api

        dsync_cmd.update_params(**params)
        return dsync_cmd

    def _get_dserial_cmds(self, src=None, pool=None, tmp_dir=None):
        """Get new DserializeCommand and DdeserializeCommand objects.

        This uses a temporary POSIX path as the intermediate step
        between serializing and deserializing.

        Args:
            src (str): source cont path.
            pool (str, optional): the destination pool.
            tmp_dir (str, optional): tmp directory for HDF5 input/output. Defaults to self.tmp

        """
        # First initialize new commands
        dserialize_cmd = DserializeCommand(self.hostlist_clients, self.workdir)
        ddeserialize_cmd = DdeserializeCommand(self.hostlist_clients, self.workdir)

        # Get an intermediate path for HDF5 file(s)
        tmp_path = self.new_posix_test_path(create=False, parent=(tmp_dir or self.tmp))

        # Set the source params for dserialize
        if src is not None:
            dserialize_cmd.update_params(src=src, output_path=tmp_path)

        # Set the destination params for ddeserialize
        if pool is not None:
            ddeserialize_cmd.update_params(src=tmp_path, pool=pool)

        return (dserialize_cmd, ddeserialize_cmd)

    def run_ior_with_params(self, api, test_file, pool=None, cont=None, **params):
        """Set the ior params and run ior.

        Args:
            api (str): DFS or POSIX API
            test_file (str): ior test_file
            pool (TestPool, optional): the pool object
            cont (TestContainer, optional): the cont or uuid
            params (dict, optional): ior params to update

        """
        # Use correct ppn and np for ior
        self.ppn = self.ior_ppn
        self.processes = self.ior_np

        # Reset params
        self.ior_cmd.get_params(self)

        # Allow cont to be either the container or the uuid
        cont_uuid = uuid_from_obj(cont) or None

        # Add params required by this method
        self.ior_cmd.update_params(api=api, test_file=test_file)

        # Set ior params for DFS
        if api == "DFS" and pool:
            self.ior_cmd.set_daos_params(self.server_group, pool, cont_uuid)

        # Update additional params
        self.ior_cmd.update_params(**params)

        self.run_ior(self.get_ior_job_manager_command(), self.processes, pool=pool,
                     display_space=False)

    def run_mdtest_with_params(self, api, test_dir, pool=None, cont=None, **params):
        """Set the mdtest params and run mdtest.

        Args:
            api (str): DFS or POSIX API
            test_dir: (str): mdtest test_dir
            pool (TestPool, optional): the pool object
            cont (TestContainer, optional): the cont or uuid
            params (dict, optional): mdtest params to update

        """
        # Use correct ppn and np for mdtest
        self.ppn = self.mdtest_ppn
        self.processes = self.mdtest_np

        # Reset params
        self.mdtest_cmd.get_params(self)

        # Allow cont to be either the container or the uuid
        cont_uuid = uuid_from_obj(cont) or None

        # Add params required by this method
        self.mdtest_cmd.update_params(api=api, test_dir=test_dir)

        # Set mdtest params for DFS
        if api == 'DFS' and pool:
            self.mdtest_cmd.set_daos_params(self.server_group, pool, cont_uuid)

        # Update additional params
        self.mdtest_cmd.update_params(**params)

        self.run_mdtest(self.get_mdtest_job_manager_command(self.manager), self.processes,
                        display_space=False, pool=pool)

    def run_diff(self, src, dst, deref=False):
        """Run Linux diff command.

        Args:
            src (str): the source path
            dst (str): the destination path
            deref (bool, optional): Whether to dereference symlinks.
                Defaults to False.

        """
        deref_str = ""
        if not deref:
            deref_str = "--no-dereference"

        cmd = "diff -r {} '{}' '{}'".format(
            deref_str, src, dst)
        self.execute_cmd(cmd)

    # pylint: disable=too-many-arguments
    def run_datamover(self, test_desc=None,
                      expected_rc=0, expected_output=None, expected_err=None,
                      processes=None, **params):
        """Run the corresponding command specified by self.tool.
        Calls set_datamover_params if and only if any are passed in.

        Args:
            test_desc (str, optional): description to print before running
            expected_rc (int, optional): rc expected to be returned
            expected_output (list, optional): substrings expected in stdout
            expected_err (list, optional): substrings expected in stderr
            processes (int, optional): number of mpi processes.
                Defaults to np for corresponding tool.
            params (dict, optional): params to update before running.

        Returns:
            The result "run" object

        """
        # Default expected_output and expected_err to empty lists
        expected_output = expected_output or []
        expected_err = expected_err or []

        # Convert singular value to list
        if not isinstance(expected_output, list):
            expected_output = [expected_output]
        if not isinstance(expected_err, list):
            expected_err = [expected_err]

        if test_desc:
            self.log.info("Running %s: %s %s", self.tool, self.test_id, test_desc)

        ppn = None
        try:
            if self.tool == "DCP":
                dcp_cmd = self._get_dcp_cmd(**params)
                if not processes:
                    processes = self.dcp_np
                    ppn = self.dcp_ppn
                # If we expect an rc other than 0, don't fail
                dcp_cmd.exit_status_exception = (expected_rc == 0)
                result = dcp_cmd.run(processes, ppn)
            elif self.tool == "DSYNC":
                dcp_cmd = self._get_dsync_cmd(**params)
                if not processes:
                    processes = self.dsync_np
                    ppn = self.dsync_ppn
                # If we expect an rc other than 0, don't fail
                dcp_cmd.exit_status_exception = (expected_rc == 0)
                result = dcp_cmd.run(processes, ppn)
            elif self.tool == "DSERIAL":
                dserialize_cmd, ddeserialize_cmd = self._get_dserial_cmds(**params)
                if processes:
                    processes1 = processes2 = processes
                    ppn1 = ppn2 = None
                else:
                    processes1 = self.dserialize_np
                    ppn1 = self.dserialize_ppn
                    processes2 = self.ddeserialize_np
                    ppn2 = self.ddeserialize_ppn
                result = dserialize_cmd.run(processes1, ppn1)
                result = ddeserialize_cmd.run(processes2, ppn2)
            elif self.tool == "FS_COPY":
                result = self.daos_cmd.filesystem_copy(**params)
            elif self.tool == "CONT_CLONE":
                result = self.daos_cmd.container_clone(**params)
            else:
                self.fail("Invalid tool: {}".format(str(self.tool)))
        except CommandFailure as error:
            self.log.error("%s command failed: %s", str(self.tool), str(error))
            self.fail("Datamover failed: {}\n".format(test_desc))

        # Check the return code
        actual_rc = result.exit_status
        if actual_rc != expected_rc:
            self.fail(
                "Expected rc={} but got rc={}: {}\n".format(expected_rc, actual_rc, test_desc))

        # Check for expected output
        for expected in expected_output:
            if expected not in result.stdout_text:
                self.fail("stdout expected {}: {}".format(expected, test_desc))
        for expected in expected_err:
            if expected not in result.stderr_text:
                self.fail("stderr expected {}: {}".format(expected, test_desc))

        return result

    def run_dm_activities_with_ior(self, tool, pool, cont, create_dataset=False):
        """Generic method to perform various datamover activities using ior.

        Args:
            tool (str): specify the tool name to be used
            pool (TestPool): source pool object
            cont (TestContainer): source container object
            create_dataset (bool): whether to create initial dataset. Defaults to False.
        """
        # Set the tool to use
        self.set_tool(tool)

        if create_dataset:
            # create initial datasets with ior
            self.run_ior_with_params("DFS", self.ior_cmd.test_file.value, pool, cont)

        # create cont2
        cont2 = self.get_container(pool, oclass=self.ior_cmd.dfs_oclass.value)

        # perform various datamover activities
        if tool == 'CONT_CLONE':
            result = self.run_datamover(
                "(cont to cont2)",
                src=format_path(pool, cont),
                dst=format_path(pool))
            read_back_cont = self.parse_create_cont_label(result.stdout_text)
            read_back_pool = pool
        elif tool == 'DSERIAL':
            # Create pool2
            pool2 = self.get_pool()
            # Use dfuse as a shared intermediate for serialize + deserialize
            dfuse_cont = self.get_container(pool, oclass=self.ior_cmd.dfs_oclass.value)
            self.start_dfuse(self.hostlist_clients, pool, dfuse_cont)

            # Serialize/Deserialize container 1 to a new cont2 in pool2
            result = self.run_datamover(
                "(cont->HDF5->cont2)",
                src=format_path(pool, cont),
                pool=pool2.identifier,
                tmp_dir=self.dfuse.mount_dir.value)

            # Get the destination cont2 uuid
            read_back_cont = self.parse_create_cont_label(result.stdout_text)
            read_back_pool = pool2
        elif tool in ['FS_COPY', 'DCP']:
            # copy from daos cont to cont2
            self.run_datamover(
                "(cont to cont2)",
                src=format_path(pool, cont),
                dst=format_path(pool, cont2))
        else:
            self.fail("Invalid tool: {}".format(tool))

        # move data from daos to posix FS and vice versa
        if tool in ['FS_COPY', 'DCP']:
            posix_path = self.new_posix_test_path(shared=True)
            # copy from daos cont2 to posix file system
            self.run_datamover(
                "(cont2 to posix)",
                src=format_path(pool, cont2),
                dst=posix_path)

            # create cont3
            cont3 = self.get_container(pool, oclass=self.ior_cmd.dfs_oclass.value)

            # copy from posix file system to daos cont3
            self.run_datamover(
                "(posix to cont3)",
                src=posix_path,
                dst=format_path(pool, cont3))
            read_back_cont = cont3
            read_back_pool = pool
        if tool in ['FS_COPY', 'DCP']:
            # the result is that a NEW directory is created in the destination
            daos_path = os.path.join(os.sep, os.path.basename(posix_path), 'testfile')
        elif tool in ['CONT_CLONE', 'DSERIAL']:
            daos_path = os.path.join(os.sep, 'testfile')
        else:
            self.fail("Invalid tool: {}".format(tool))
        # update ior params, read back and verify data from cont3
        self.run_ior_with_params(
            "DFS", daos_path, read_back_pool, read_back_cont, flags="-r -R -F -k")
