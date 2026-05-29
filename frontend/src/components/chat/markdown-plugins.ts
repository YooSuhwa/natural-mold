import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'

export const CHAT_STREAMING_REMARK_PLUGINS = [remarkGfm, remarkBreaks]
export const CHAT_FINAL_REMARK_PLUGINS = [remarkGfm, remarkMath, remarkBreaks]
