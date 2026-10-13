"""Plain-language guidance for the next useful studio action."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Guide:
    title: str
    hint: str
    label: str
    action: str = ''
    alternative_label: str = ''
    alternative: str = ''


def next_step(*, step, busy, exporting, media, available, video, count, pending,
              incomplete, resumable, ready, translation_setup, online, original):
    if busy:
        return Guide('正在保存视频' if exporting else '正在生成字幕',
                     '下方可以查看进度。需要中断时点击「取消」；已完成的字幕会保留。', '请稍候…')
    if not media:
        return Guide('先选一个视频或音频', '文件选好后会自动创建工程，名称使用文件名。以后可从工程库继续。',
                     '选择视频或音频', 'import')
    if not available:
        return Guide('找不到原视频或音频', '文件可能被移动了。重新选择原文件后即可继续；已有字幕仍保存在工程中。',
                     '重新选择原文件', 'relink', '保存字幕文件' if count and not incomplete and (original or not pending) else '', 'export')
    if incomplete:
        return Guide('上次的字幕还没有生成完', '已完成的进度已保存，继续即可。' if resumable else '此工程无法继续上次进度，请重新生成字幕。',
                     '继续生成字幕' if resumable else '重新生成字幕', 'resume' if resumable else 'generate')
    if not count:
        if not ready:
            return Guide('第一次使用，先准备识别功能', '打开功能准备窗口，保留默认选项，下载缺少的组件后保存。',
                         '准备识别功能', 'models')
        if translation_setup:
            return Guide('中文翻译还需要设置',
                         '填写在线翻译服务的信息后即可生成双语字幕。也可以先生成原文。' if online else '本地翻译功能尚未准备好。也可以先生成原文，稍后补上中文。',
                         '设置中文翻译' if online else '准备中文翻译', 'translation', '先生成原文字幕', 'original_generate')
        return Guide('文件已选好，可以生成字幕了', '语言不确定就用「自动识别」。默认已选智能断句，其余选项通常不用改。',
                     '开始生成字幕', 'generate')
    if pending and not (step == 2 and original):
        return Guide(f'还有 {pending} 条中文字幕待补全', '补全只处理缺少的译文。也可以先保存原文字幕，稍后继续翻译。',
                     '补全中文字幕', 'resume', '先导出原文', 'original_export')
    if step == 0:
        return Guide(f'已生成 {count} 条字幕', '字幕已经自动保存。下一步试听几句，检查文字有没有识别错。',
                     '下一步：检查字幕', 'review')
    if step == 1:
        return Guide('试听、改错，满意后保存成品', '单击一条字幕定位，点「播放」试听；双击文字修改。修改会自动保存。',
                     '下一步：保存成品', 'export_step')
    if video:
        return Guide('选一种方式保存成品', '想发给别人直接播放，选带字幕的视频；想在播放器或剪辑软件中使用，选字幕文件。',
                     '保存带字幕的视频', 'video_export', '保存字幕文件', 'export')
    return Guide('把字幕保存成文件', '音频文件可以保存 SRT / VTT 字幕，供播放器或剪辑软件使用。', '保存字幕文件', 'export')
