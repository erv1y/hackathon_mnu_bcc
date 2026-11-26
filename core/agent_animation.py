"""Offline 3-D scene driven by the existing progress callback. No AI calls."""
from dataclasses import dataclass
from pathlib import Path
import json
from html import escape

@dataclass
class Scene:
    mode: str = 'deep'
    phase: str = 'prepare'
    cached: bool = False
    previous: str = 'prepare'

    def update(self, event):
        old = self.phase
        p, stage = event.percent, event.stage
        if stage == 'Cache Hit':
            self.cached = True
            self.phase = 'cache'
        elif self.cached:
            self.phase = 'cache'
        elif stage == 'Resume':
            self.phase = 'resume'
        elif p >= 85:
            self.phase = 'report'
        elif self.mode == 'quick':
            if p >= 15:
                self.phase = 'extract'
        elif p >= 75:
            self.phase = 'audit'
        elif p == 55:
            if 'Quality Analyst' in event.message:
                self.phase = 'quality_done'
            elif 'Consistency Analyst' in event.message:
                self.phase = 'consistency_done'
        elif p >= 35:
            self.phase = 'split'
        elif p >= 15:
            self.phase = 'extract'
        if old != self.phase:
            self.previous = old
        return old != self.phase

    def finish(self, phase):
        new = 'cache' if self.cached and phase in ('report','done') else phase
        if new != self.phase:
            self.previous, self.phase = self.phase, new

# Order: Kazakh, Russian, English. UI labels only; no document content.
TEXT = {
 'prepare': ('Құжат қабылдануда','Приём документа','Receiving document'),
 'extract': ('Талаптарды шығару','Извлечение требований','Extracting requirements'),
 'quick_extract': ('Алдын ала тексеру','Предварительная проверка','Preliminary review'),
 'split': ('Екі талдаушы қатар жұмыс істейді','Два аналитика работают параллельно','Two analysts working in parallel'),
 'quality_done': ('Сапа тексерілді, сәйкестік тексерілуде','Качество проверено, согласованность в работе','Quality complete; consistency in progress'),
 'consistency_done': ('Сәйкестік тексерілді, сапа тексерілуде','Согласованность проверена, качество в работе','Consistency complete; quality in progress'),
 'audit': ('Қорытынды аудит','Итоговый аудит','Final audit'),
 'report': ('Есеп дайындалуда','Формирование отчёта','Preparing report'),
 'done': ('Есеп дайын','Отчёт готов','Report ready'),
 'cache': ('Кэштен қалпына келтірілді','Восстановлено из кэша','Restored from cache'),
 'resume': ('Сақталған күй қалпына келтірілуде','Восстановление состояния','Resuming saved state'),
 'error': ('Тексеру тоқтатылды','Проверка остановлена','Validation stopped'),
 'export_error': ('JSON сақталды, PDF қолжетімсіз','JSON сохранён, PDF недоступен','JSON saved; PDF unavailable'),
 'inbox': ('Кіріс пошта','Входящая почта','Mail / inbox'),
 'outbox': ('Дайын есеп','Готовый отчёт','Outgoing report'),
 'lounge': ('Демалыс бөлмесі','Комната отдыха','Lounge'),
 'waiting': ('Күтуде','Ожидает','Waiting'),
 'working': ('Жұмыс істеуде','В работе','Working'),
 'complete': ('Аяқталды','Завершено','Complete'),
 'foot': ('Көрініс нақты кезеңдерге байланысты. Қозғалыс — шартты бейнелеу, қалған уақыт емес.','Сцена привязана к реальным этапам. Движение условное, не таймер оставшегося времени.','Scene follows real stages. Motion is illustrative, not a time estimate.'),
 'preview': ('Көріністі алдын ала қарау','Предпросмотр сцены','Scene preview'),
 'back': ('Артқа','Назад','Back'), 'next': ('Алға','Далее','Next'),
 'auto': ('Авто','Авто','Auto'), 'pause': ('Кідірту','Пауза','Pause'),
}

def render_scene(scene, lang='kk', preview=False):
    idx = {'kk':0,'ru':1,'en':2}.get(lang,0)
    labels = {k:v[idx] for k,v in TEXT.items()}
    data = dict(mode=scene.mode, phase=scene.phase, previous=scene.previous, labels=labels, preview=preview)
    template = Path(__file__).with_name('office_scene.html').read_text(encoding='utf-8')
    return template.replace('__SCENE_CONFIG__', json.dumps(data, ensure_ascii=True).replace('<','\\u003c')).replace('__LANG__',escape(lang if lang in ('kk','ru','en') else 'kk'))
