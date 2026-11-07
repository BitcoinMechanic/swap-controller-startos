import { VersionGraph } from '@start9labs/start-sdk'
import { current } from './current'
import { v_privkeyio_0_1_0_1 } from './v_privkeyio0.1.0_1'
import { v_privkeyio_0_1_0_0 } from './v_privkeyio0.1.0_0'
export const versionGraph = VersionGraph.of({ current, other: [v_privkeyio_0_1_0_0, v_privkeyio_0_1_0_1] })
