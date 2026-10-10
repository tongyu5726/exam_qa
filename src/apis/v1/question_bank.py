"""我的题库 API：课程隔离的题目生成、题目管理与组卷。"""

from fastapi import APIRouter, Depends, Query
from starlette import status

from src.exceptions import NotFoundException
from src.dependencies import (
    get_catalog_store,
    get_current_user,
    get_llm_client,
    get_question_bank_store,
    get_vector_store,
    get_doc_store,
)
from src.models import (
    PaperCreateRequest,
    PaperAssembleData,
    PaperAssembleRequest,
    PaperData,
    QuestionCreateRequest,
    QuestionData,
    QuestionGenerateData,
    QuestionGenerateRequest,
    QuestionPatch,
    PracticeStartRequest,
    PracticeSubmitRequest,
    PracticeAssessmentRequest,
)
from src.services.question_bank import assemble_paper, generate_questions
from src.services.storage.catalog_store import CatalogStore
from src.services.storage.question_bank_store import QuestionBankStore
from src.services.storage.vector_store import ChromaVectorStore
from src.services.llm import OpenAIClient
from src.services import practice
from src.services.storage.doc_store import SQLiteDocStore

router = APIRouter(prefix="/question-bank", tags=["question-bank"])


@router.post('/practice')
async def start_practice(
    body: PracticeStartRequest,
    store: QuestionBankStore = Depends(get_question_bank_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    return {'code': status.HTTP_200_OK, 'data': practice.present_practice(practice.start_practice(body, store), ds)}


@router.get('/practice/{attempt_id}')
async def get_practice(
    attempt_id: str, course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    return {'code': status.HTTP_200_OK, 'data': practice.present_practice(store.get_practice(attempt_id, course_id), ds)}


@router.post('/practice/{attempt_id}/submit')
async def submit_practice(
    attempt_id: str, body: PracticeSubmitRequest,
    store: QuestionBankStore = Depends(get_question_bank_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    return {'code': status.HTTP_200_OK, 'data': practice.present_practice(practice.submit_practice(attempt_id, body, store), ds)}


@router.post('/practice/{attempt_id}/assessment')
async def assess_practice(
    attempt_id: str, body: PracticeAssessmentRequest,
    store: QuestionBankStore = Depends(get_question_bank_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    return {'code': status.HTTP_200_OK, 'data': practice.present_practice(practice.assess_practice(attempt_id, body.course_id, body.correct, store), ds)}


@router.get('/practice-review')
async def practice_review(
    course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    return {'code': status.HTTP_200_OK, 'data': practice.practice_review(course_id, store, ds)}


@router.post("/generate")
async def generate_question_drafts(
    body: QuestionGenerateRequest,
    vs: ChromaVectorStore = Depends(get_vector_store),
    llm: OpenAIClient = Depends(get_llm_client),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    result = generate_questions(request=body, vs=vs, llm=llm, store=store)
    return {"code": status.HTTP_200_OK, "data": QuestionGenerateData(**result).model_dump()}


@router.post("/questions")
async def create_question(
    body: QuestionCreateRequest,
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    question = store.create_question({**body.model_dump(), "origin": "manual"})
    return {"code": status.HTTP_200_OK, "data": QuestionData(**question).model_dump()}


@router.get("/questions")
async def list_questions(
    course_id: str = Query(...),
    question_type: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
    chapter: str | None = Query(default=None),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    questions = store.list_questions(course_id, question_type=question_type, difficulty=difficulty, chapter=chapter)
    return {"code": status.HTTP_200_OK, "data": [QuestionData(**question).model_dump() for question in questions]}


@router.get("/questions/{question_id}")
async def get_question(
    question_id: str,
    course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    return {"code": status.HTTP_200_OK, "data": QuestionData(**store.require_question(question_id, course_id)).model_dump()}


@router.patch("/questions/{question_id}")
async def patch_question(
    question_id: str,
    body: QuestionPatch,
    course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    question = store.update_question(question_id, course_id, body.model_dump(exclude_unset=True))
    return {"code": status.HTTP_200_OK, "data": QuestionData(**question).model_dump()}


@router.delete("/questions/{question_id}")
async def delete_question(
    question_id: str,
    course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    store.delete_question(question_id, course_id)
    return {"code": status.HTTP_200_OK, "data": None}


@router.post("/papers")
async def create_paper(
    body: PaperCreateRequest,
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    paper = store.create_paper(body.course_id, body.title, body.description, [item.model_dump() for item in body.items])
    return {"code": status.HTTP_200_OK, "data": PaperData(**paper).model_dump()}


@router.post("/papers/assemble")
async def assemble_paper_by_blueprint(
    body: PaperAssembleRequest,
    vs: ChromaVectorStore = Depends(get_vector_store),
    llm: OpenAIClient = Depends(get_llm_client),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    result = assemble_paper(request=body, vs=vs, llm=llm, store=store)
    return {"code": status.HTTP_200_OK, "data": PaperAssembleData(**result).model_dump()}


@router.get("/papers")
async def list_papers(
    course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    return {"code": status.HTTP_200_OK, "data": [PaperData(**paper).model_dump() for paper in store.list_papers(course_id)]}


@router.get("/papers/{paper_id}")
async def get_paper(
    paper_id: str,
    course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    paper = store.get_paper(paper_id, course_id)
    if paper is None:
        raise NotFoundException("试卷不存在或不属于当前课程")
    return {"code": status.HTTP_200_OK, "data": PaperData(**paper).model_dump()}


@router.delete("/papers/{paper_id}")
async def delete_paper(
    paper_id: str,
    course_id: str = Query(...),
    store: QuestionBankStore = Depends(get_question_bank_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    store.delete_paper(paper_id, course_id)
    return {"code": status.HTTP_200_OK, "data": None}
