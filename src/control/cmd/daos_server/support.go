//
// (C) Copyright 2019-2022 Intel Corporation.
//
// SPDX-License-Identifier: BSD-2-Clause-Patent
//

package main

import (
	"github.com/daos-stack/daos/src/control/common/cmdutil"
	"github.com/daos-stack/daos/src/control/lib/support"
)

type SupportCmd struct {
	CollectLog collectLogCmd `command:"collectlog" description:"Collect logs from server"`
}

// collectLogCmd is the struct representing the command to scan the machine for network interface devices
// that match the given fabric provider.
type collectLogCmd struct {
	optCfgCmd
	cmdutil.LogCmd
	TargetFolder string `short:"s" long:"loglocation" description:"Folder location where log is going to be copied"`
}

func (cmd *collectLogCmd) Execute(_ []string) error {
	if cmd.TargetFolder == "" {
		cmd.TargetFolder = "/tmp/daos_support_logs"
	}

	err := support.CollectDaosLog(cmd.TargetFolder)
	if err != nil {
		return err
	}

	return nil
}
