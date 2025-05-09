import importlib.metadata
from contextlib import asynccontextmanager
from logging import getLogger
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor as Instrumentor
from starlette.exceptions import HTTPException as StarletteHTTPException
from tortoise.exceptions import ValidationError

from goosebit import api, db, ui, updater
from goosebit.auth import get_user_from_request, login_user, redirect_if_authenticated
from goosebit.settings import PWD_CXT, config
from goosebit.ui.nav import nav
from goosebit.ui.static import static
from goosebit.ui.templates import templates
from goosebit.users import create_initial_user

logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db_ready = await db.init()
    if not db_ready:
        logger.exception("DB does not exist, try running `poetry run aerich upgrade`.")
    if db_ready:
        yield
    await db.close()


app = FastAPI(
    title="gooseBit",
    summary="A simplistic, opinionated remote update server implementing hawkBit™'s DDI API.",
    version=importlib.metadata.version("goosebit"),
    lifespan=lifespan,
    license_info={
        "name": "Apache 2.0",
        "identifier": "Apache-2.0",
    },
    redoc_url=None,
    docs_url=None,
    openapi_tags=[
        {
            "name": "login",
            "description": "API authentication.  "
            "Can be used in the `authorization` header, in the format `{token_type} {access_token}`.",
        }
    ],
)
app.include_router(updater.router)
app.include_router(ui.router)
app.include_router(api.router)
app.mount("/static", static, name="static")
Instrumentor.instrument_app(app)


# Custom exception handler for Tortoise ValidationError
@app.exception_handler(ValidationError)
async def tortoise_validation_exception_handler(request: Request, exc: ValidationError):
    raise HTTPException(422, str(exc))


# Extend default handler to do logging
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    logger.warning(f"HTTPException, request={request.url}, status={exc.status_code}, detail={exc.detail}")
    return await http_exception_handler(request, exc)


@app.middleware("http")
async def attach_user(request: Request, call_next):
    request.scope["user"] = await get_user_from_request(request)
    return await call_next(request)


@app.middleware("http")
async def attach_nav(request: Request, call_next):
    request.scope["nav"] = nav.get()
    return await call_next(request)


@app.middleware("http")
async def attach_config(request: Request, call_next):
    request.scope["config"] = config
    return await call_next(request)


@app.get("/", include_in_schema=False)
def root_redirect(request: Request):
    return RedirectResponse(request.url_for("ui_root"))


@app.get("/login", include_in_schema=False, dependencies=[Depends(redirect_if_authenticated)])
async def login_get(request: Request):
    return templates.TemplateResponse(request, "login.html.jinja")


@app.post("/login", tags=["login"])
async def login_post(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    return {"access_token": await login_user(form_data.username, form_data.password), "token_type": "bearer"}


@app.get("/setup", include_in_schema=False)
async def setup_get(request: Request):
    return templates.TemplateResponse(request, "setup.html.jinja")


@app.post("/setup", include_in_schema=False)
async def setup_post(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    await create_initial_user(form_data.username, PWD_CXT.hash(form_data.password))
    return {"access_token": await login_user(form_data.username, form_data.password), "token_type": "bearer"}


@app.get("/logout", include_in_schema=False)
async def logout(request: Request):
    resp = RedirectResponse(request.url_for("login_get"), status_code=302)
    resp.delete_cookie(key="session_id")
    return resp


@app.get("/docs")
async def swagger_docs(request: Request):
    return get_swagger_ui_html(
        title="gooseBit docs",
        openapi_url="/openapi.json",
        swagger_favicon_url=str(request.url_for("static", path="/favicon.svg")),
        swagger_ui_parameters={"operationsSorter": "alpha"},
    )
