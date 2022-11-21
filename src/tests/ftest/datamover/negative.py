'''
  (C) Copyright 2020-2022 Intel Corporation.

  SPDX-License-Identifier: BSD-2-Clause-Patent
'''

from os.path import join
import uuid

from data_mover_test_base import DataMoverTestBase
from duns_utils import format_path


class DmvrNegativeTest(DataMoverTestBase):
    # pylint: disable=too-many-ancestors
    """Test class for POSIX DataMover negative testing.

    Test Class Description:
        Tests the following cases:
            Bad parameters.
            Simple error checking.
    :avocado: recursive
    """

    # DCP error codes
    MFU_ERR_DCP_COPY = "MFU_ERR(-1101)"
    MFU_ERR_DAOS_INVAL_ARG = "MFU_ERR(-4001)"

    def setUp(self):
        """Set up each test case."""
        # Start the servers and agents
        super().setUp()

        # Get the parameters
        self.test_file = self.ior_cmd.test_file.value

        # Setup the directory structures
        self.new_posix_test_path()
        self.posix_test_file = join(self.posix_local_test_paths[0], self.test_file)
        self.daos_test_path = "/"
        self.daos_test_file = join(self.daos_test_path, self.test_file)

    def test_dm_bad_params_dcp(self):
        """Jira ID: DAOS-5515 - Initial test case.
           Jira ID: DAOS-6355 - Test case reworked.
           Jira ID: DAOS-9874 - daos-prefix removed.
        Test Description:
            Test POSIX copy with invalid parameters.
            This uses the dcp tool.
            (1) Bad parameter: required argument
            (2) Bad parameter: source is destination.
            (3) Bad parameter: UUID, UNS, or POSIX path is invalid.
        :avocado: tags=all,full_regression
        :avocado: tags=vm
        :avocado: tags=datamover,mfu,mfu_dcp,dfuse,dfs,ior
        :avocado: tags=dm_negative,dm_bad_params_dcp,test_dm_bad_params_dcp
        """
        self.set_tool("DCP")

        # Start dfuse to hold all pools/containers
        self.start_dfuse(self.hostlist_clients)

        # Create a test pool
        pool1 = self.create_pool()

        # Create a special container to hold UNS entries
        uns_cont = self.get_container(pool1)

        # Create a test container
        cont1_path = join(self.dfuse.mount_dir.value, pool1.uuid, uns_cont.uuid, 'uns1')
        cont1 = self.get_container(pool1, path=cont1_path)

        # Create test files
        self.run_ior_with_params("POSIX", self.posix_test_file)
        self.run_ior_with_params("DFS", self.daos_test_file, pool1, cont1)

        # Bad parameter: required arguments.
        self.run_datamover(
            "(missing source pool)",
            src=format_path(),
            dst=self.posix_local_test_paths[0],
            expected_rc=1,
            expected_output=self.MFU_ERR_DAOS_INVAL_ARG)

        self.run_datamover(
            "(missing source cont)",
            src=format_path(pool1),
            dst=self.posix_local_test_paths[0],
            expected_rc=1,
            expected_output=self.MFU_ERR_DAOS_INVAL_ARG)

        self.run_datamover(
            "(missing dest pool)",
            src=self.posix_local_test_paths[0],
            dst=format_path(),
            expected_rc=1,
            expected_output=self.MFU_ERR_DAOS_INVAL_ARG)

        # (2) Bad parameter: source is destination.
        self.run_datamover(
            "(UUID source is UUID dest)",
            src=format_path(pool1.uuid, cont1.uuid),
            dst=format_path(pool1.uuid, cont1.uuid),
            expected_rc=1,
            expected_output=self.MFU_ERR_DAOS_INVAL_ARG)

        self.run_datamover(
            "(UNS source is UNS dest)",
            src=cont1.path.value,
            dst=cont1.path.value,
            expected_rc=1,
            expected_output=self.MFU_ERR_DAOS_INVAL_ARG)

        self.run_datamover(
            "(UUID source is UNS dest)",
            src=format_path(pool1.uuid, cont1.uuid),
            dst=cont1.path.value,
            expected_rc=1,
            expected_output=self.MFU_ERR_DAOS_INVAL_ARG)

        self.run_datamover(
            "(UNS source is UUID dest)",
            src=cont1.path.value,
            dst=format_path(pool1.uuid, cont1.uuid),
            expected_rc=1,
            expected_output=self.MFU_ERR_DAOS_INVAL_ARG)

        # (3) Bad parameter: UUID, UNS, or POSIX path does not exist.
        fake_uuid = str(uuid.UUID(int=0))
        self.run_datamover(
            "(invalid source pool)",
            src=format_path(fake_uuid, cont1),
            dst=self.posix_local_test_paths[0],
            expected_rc=1,
            expected_output="DER_NONEXIST")

        self.run_datamover(
            "(invalid source cont)",
            src=format_path(pool1, fake_uuid),
            dst=self.posix_local_test_paths[0],
            expected_rc=1,
            expected_output="DER_NONEXIST")

        self.run_datamover(
            "(invalid dest pool)",
            src=self.posix_local_test_paths[0],
            dst=format_path(fake_uuid, cont1),
            expected_rc=1,
            expected_output="DER_NONEXIST")

        self.run_datamover(
            "(invalid source cont path)",
            src=format_path(pool1, cont1, "/fake/fake"),
            dst=self.posix_local_test_paths[0],
            expected_rc=1,
            expected_output="No such file or directory")

        self.run_datamover(
            "(invalid source cont UNS path)",
            src=cont1.path.value + "/fake/fake",
            dst=self.posix_local_test_paths[0],
            expected_rc=1,
            expected_output="No such file or directory")

        self.run_datamover(
            "(invalid dest cont path)",
            src=self.posix_local_test_paths[0],
            dst=format_path(pool1, cont1, "/fake/fake"),
            expected_rc=1,
            expected_output="No such file or directory")

        self.run_datamover(
            "(invalid source posix path)",
            src="/fake/fake",
            dst=format_path(pool1, cont1),
            expected_rc=1,
            expected_output="No such file or directory")

        self.run_datamover(
            "(invalid dest posix path)",
            src=format_path(pool1, cont1),
            dst="/fake/fake",
            expected_rc=1,
            expected_output="No such file or directory")

    def test_dm_negative_error_check_dcp(self):
        """Jira ID: DAOS-5515
        Test Description:
            Tests POSIX copy error checking for dcp.
            Tests the following cases:
                destination filename is invalid.
        :avocado: tags=all,full_regression
        :avocado: tags=vm
        :avocado: tags=datamover,dcp
        :avocado: tags=dm_negative,dm_negative_error_check_dcp
        """
        self.set_tool("DCP")

        # Create pool and containers
        pool1 = self.create_pool()
        cont1 = self.get_container(pool1)

        # Create source file
        self.run_ior_with_params("DFS", self.daos_test_file, pool1, cont1)

        # Use a really long filename
        dst_path = join(self.posix_local_test_paths[0], "d" * 300)
        self.run_datamover(
            "(filename is too long)",
            src=format_path(pool1, cont1),
            dst=dst_path,
            expected_rc=1,
            expected_output=[self.MFU_ERR_DCP_COPY, "errno=36"])
