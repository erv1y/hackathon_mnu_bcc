from typing import Literal

Language = Literal["ru", "kk", "en"]
LANGUAGE_NAMES = {"ru": "русском", "kk": "казахском", "en": "английском"}
MISSING_QUOTES = {
    "ru": "Отсутствует в тексте ТЗ",
    "kk": "Құжатта көрсетілмеген",
    "en": "Not specified in the document",
}
REPORT_COPY = {
    "ru": {
        "replay": "Сохранённый пример",
        "local": "локально",
        "formula": "100 минус сумма баллов за учтённые замечания, минимум 0.",
    },
    "kk": {
        "replay": "Сақталған мысал",
        "local": "жергілікті",
        "formula": "100 ұпайдан есепке алынған ескертулердің ұпайлары шегеріледі. Ең төменгі мән — 0.",
    },
    "en": {
        "replay": "Saved example",
        "local": "local",
        "formula": "Subtract the points for included findings from 100, with a minimum of 0.",
    },
}

_ERROR_COPY = {
    "document_count": (
        "Можно загрузить до {limit} файлов за один запуск.",
        "Бір тексеруде {limit} файлға дейін жүктеуге болады.",
        "You can upload up to {limit} files per review.",
    ),
    "document_selection": (
        "Передайте текст, один файл, список файлов или сохранённый пакет.",
        "Мәтінді, бір файлды, файлдар тізімін немесе сақталған пакетті жіберіңіз.",
        "Submit text, a single file, a list of files, or a saved package.",
    ),
    "document_roles": (
        "Укажите по одной роли для каждого файла.",
        "Әр файл үшін бір рөлді көрсетіңіз.",
        "Supply one role per uploaded file.",
    ),
    "invalid_package": (
        "Не удалось прочитать сохранённый пакет документов.",
        "Сақталған құжаттар пакетін оқу мүмкін болмады.",
        "The saved document package is invalid.",
    ),
    "text_limit": (
        "Текст длиннее {limit} знаков. Разделите документ на части.",
        "Мәтін {limit} таңбадан ұзын. Құжатты бөліктерге бөліңіз.",
        "The text has more than {limit} characters. Split the document into sections.",
    ),
    "pdf_damaged": (
        "Не удалось открыть PDF. Проверьте, открывается ли файл.",
        "PDF файлын ашу мүмкін болмады. Файлдың ашылатынын тексеріңіз.",
        "Could not open the PDF. Check that the file opens.",
    ),
    "pdf_password": (
        "PDF защищён паролем. Снимите защиту и загрузите файл снова.",
        "PDF құпиясөзбен қорғалған. Қорғанысты алып, файлды қайта жүктеңіз.",
        "The PDF is password protected. Remove the protection and upload it again.",
    ),
    "pdf_limit": (
        "В PDF больше {limit} страниц. Загрузите документ по частям.",
        "PDF файлында {limit} беттен көп. Құжатты бөліктерге бөліп жүктеңіз.",
        "The PDF has more than {limit} pages. Upload it in parts.",
    ),
    "pdf_no_text": (
        "В PDF не найден текстовый слой. Вставьте текст или загрузите DOCX.",
        "PDF файлында мәтін табылмады. Мәтінді енгізіңіз немесе DOCX файлын жүктеңіз.",
        "No readable text was found in the PDF. Paste the text or upload a DOCX.",
    ),
    "pdf_partial": (
        "Не удалось извлечь текст со страниц PDF: {pages}.",
        "PDF файлының мына беттерінен мәтін алынбады: {pages}.",
        "Could not read text on PDF pages: {pages}.",
    ),
    "docx_limit": (
        "Документ Word слишком велик после распаковки. Загрузите его по частям.",
        "Word құжатының ашылғандағы көлемі тым үлкен. Оны бөліктерге бөліп жүктеңіз.",
        "The expanded Word document is too large. Upload it in parts.",
    ),
    "docx_damaged": (
        "Не удалось открыть DOCX. Проверьте формат документа.",
        "DOCX файлын ашу мүмкін болмады. Құжат пішімін тексеріңіз.",
        "Could not open the DOCX. Check the document format.",
    ),
    "file_empty": ("Загруженный файл пустой.", "Жүктелген файл бос.", "The uploaded file is empty."),
    "old_format": (
        "Этот формат не поддерживается. Сохраните документ как DOCX или PDF.",
        "Бұл пішім қолдау таппайды. Құжатты DOCX немесе PDF түрінде сақтаңыз.",
        "This format is unsupported. Save the document as DOCX or PDF.",
    ),
    "unsupported": (
        "Используйте PDF, DOCX, TXT или MD.",
        "PDF, DOCX, TXT немесе MD файлын пайдаланыңыз.",
        "Use a PDF, DOCX, TXT or MD file.",
    ),
    "extraction_empty": (
        "Из файла не удалось извлечь текст.",
        "Файлдан мәтін алу мүмкін болмады.",
        "No text could be extracted from the file.",
    ),
    "upload_limit": (
        "Файл больше {limit} МБ. Выберите файл поменьше.",
        "Файл көлемі {limit} МБ-тан асады. Көлемі кішірек файл таңдаңыз.",
        "The file exceeds {limit} MB. Choose a smaller file.",
    ),
    "file_unreadable": (
        "Не удалось прочитать файл. Проверьте, открывается ли он.",
        "Файлды оқу мүмкін болмады. Оның ашылатынын тексеріңіз.",
        "Could not read the file. Check that it opens.",
    ),
    "missing_input": (
        "Вставьте текст или загрузите файл.",
        "Мәтінді енгізіңіз немесе файл жүктеңіз.",
        "Paste the text or upload a file.",
    ),
    "short_text": (
        "Текст слишком короткий. Нужно хотя бы {limit} знаков.",
        "Мәтін тым қысқа. Кемінде {limit} таңба қажет.",
        "The text is too short. Enter at least {limit} characters.",
    ),
    "context_limit": (
        "Сократите детали о проекте до {limit} знаков.",
        "Жоба туралы қосымша мәліметті {limit} таңбаға дейін қысқартыңыз.",
        "Shorten the project details to {limit} characters.",
    ),
    "body_limit": ("Запрос слишком большой.", "Сұрау көлемі тым үлкен.", "The request is too large."),
    "body_size": ("Некорректный размер запроса.", "Сұрау көлемі қате.", "The request size is invalid."),
    "model_unavailable": (
        "Сервис проверки недоступен. Обратитесь к владельцу стенда.",
        "Тексеру қызметі қолжетімсіз. Стенд иесіне хабарласыңыз.",
        "The review service is unavailable. Contact the site owner.",
    ),
    "model_timeout": (
        "Сервис не успел ответить. Попробуйте проверить документ по частям.",
        "Қызмет жауап беріп үлгермеді. Құжатты бөліктерге бөліп тексеріп көріңіз.",
        "The service did not respond in time. Try reviewing the document in parts.",
    ),
    "model_limit": (
        "Сервис проверки достиг лимита запросов. Попробуйте позже.",
        "Тексеру қызметінің сұрау шегіне жетті. Кейін қайталап көріңіз.",
        "The review service reached its request limit. Try again later.",
    ),
    "model_response": (
        "Сервис вернул неполный ответ. Попробуйте ещё раз.",
        "Қызмет толық жауап бермеді. Қайталап көріңіз.",
        "The service returned an incomplete response. Try again.",
    ),
    "model_refusal": (
        "Сервис анализа отказался обработать документ.",
        "Талдау қызметі құжатты өңдеуден бас тартты.",
        "The review service declined to process the document.",
    ),
}
_LANG_INDEX = {"ru": 0, "kk": 1, "en": 2}


def error_text(language: Language, kind: str, **values: object) -> str:
    return _ERROR_COPY[kind][_LANG_INDEX[language]].format(**values)
