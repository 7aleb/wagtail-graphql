# python
import string
from typing import Type, Set
# django
from django.utils.text import capfirst
# graphene
import graphene
from graphene.types.generic import GenericScalar
# graphene_django
from graphene_django import DjangoObjectType
# wagtail
from wagtail.core.fields import StreamField
from wagtail.core.models import Page as wagtailPage
# wagtail forms
from wagtail.contrib.forms.models import AbstractForm
# wagtail settings
from wagtail.contrib.settings.models import BaseSetting
# app
from .registry import registry
from .permissions import with_page_permissions
from .types.settings import settings_registry
# app types
from .types import (
    PageInterface,
    Settings,
    FormError,
    FormField,
)


def _add_form(cls: Type[AbstractForm], node: str, dict_params: dict, url_prefix: str) -> Type[graphene.Mutation]:
    registry.page_prefetch_fields.add(cls.__name__.lower())
    dict_params['Meta'].interfaces = (PageInterface,)
    dict_params['form_fields'] = graphene.List(FormField)

    def form_fields(self, _info):
        return list(FormField(name=field_.clean_name, field_type=field_.field_type)
                    for field_ in self.form_fields.all())

    dict_params['resolve_form_fields'] = form_fields
    tp = type(node, (DjangoObjectType,), dict_params)
    registry.pages[cls] = tp

    args = type("Arguments", (), {'values': GenericScalar(),
                                  "url": graphene.String(required=True)})
    _node = node

    def mutate(_self, info, url, values):
        query = wagtailPage.objects.filter(url_path=url_prefix + url.rstrip('/') + '/')
        instance = with_page_permissions(
            info.context,
            query.specific()
        ).live().first()
        user = info.context.user
        form = instance.get_form(values, None, page=instance, user=user)
        if form.is_valid():
            # form_submission
            instance.process_form_submission(form)
            return registry.forms[_node](result="OK")
        else:
            return registry.forms[_node](result="FAIL", errors=[FormError(*err) for err in form.errors.items()])

    dict_params = {
        "Arguments": args,
        "mutate": mutate,
        "result": graphene.String(),
        "errors": graphene.List(FormError),
    }
    tp = type(node + "Mutation", (graphene.Mutation,), dict_params)  # type: Type[Mutation]
    registry.forms[node] = tp
    return tp


def _add_page(cls: Type[wagtailPage], node: str, dict_params: dict) -> Type[DjangoObjectType]:
    registry.page_prefetch_fields.add(cls.__name__.lower())
    dict_params['Meta'].interfaces = (PageInterface,)
    tp = type(node, (DjangoObjectType,), dict_params)
    registry.pages[cls] = tp
    return tp  # type: Type[DjangoObjectType]


def _add_setting(cls: Type[BaseSetting], node: str, dict_params: dict) -> Type[DjangoObjectType]:
    dict_params['Meta'].interfaces = (Settings,)
    tp = type(node, (DjangoObjectType,), dict_params)  # type: Type[DjangoObjectType]
    registry.settings[capfirst(cls._meta.verbose_name)] = cls
    return tp


def _add_snippet(cls: type, node: str, dict_params: dict) -> Type[DjangoObjectType]:
    tp = type(node, (DjangoObjectType,), dict_params)  # type: Type[DjangoObjectType]
    registry.snippets[cls] = tp
    registry.snippets_by_name[node] = tp
    return tp


def _add_django_model(_cls: type, node: str, dict_params: dict) -> Type[DjangoObjectType]:
    tp = type(node, (DjangoObjectType,), dict_params)  # type: Type[DjangoObjectType]
    registry.django[node] = tp
    return tp


def _add_streamfields(cls: type, node: str, dict_params: dict, app: str, prefix: str) -> None:
    from .types.streamfield import (
        block_handler,
        stream_field_handler,
    )

    for field in cls._meta.fields:
        if isinstance(field, StreamField):
            field_name = field.name
            stream_field_name = f"{node}{string.capwords(field_name, sep='_').replace('_', '')}"
            blocks = field.stream_block.child_blocks

            handlers = dict(
                (name, block_handler(block, app, prefix))
                for name, block in blocks.items()
            )

            f, resolve = stream_field_handler(
                stream_field_name,
                field_name,
                handlers
            )

            dict_params.update({
                field.name: f,
                "resolve_" + field.name: resolve
            })


def _register_model(registered: Set[type], cls: type, snippet: bool,
                    app: str, prefix: str, url_prefix: str) -> None:
    if cls in registered:
        return

    prefix = prefix.format(app=string.capwords(app),
                           cls=cls.__name__)
    node = prefix + cls.__name__

    # dict parameters to create GraphQL type
    class Meta:
        model = cls
        interfaces = tuple()  # type: tuple

    dict_params = {'Meta': Meta}

    # add streamfield handlers
    _add_streamfields(cls, node, dict_params, app, prefix)

    if snippet:
        _add_snippet(cls, node, dict_params)
    elif issubclass(cls, AbstractForm):
        _add_form(cls, node, dict_params, url_prefix)
    elif issubclass(cls, wagtailPage):
        _add_page(cls, node, dict_params)
    elif issubclass(cls, BaseSetting):
        _add_setting(cls, node, dict_params)
    else:  # Django Model
        _add_django_model(cls, node, dict_params)

    registered.add(cls)


def add_app(app: str, prefix: str = '{app}', url_prefix: str = '') -> None:
    from django.contrib.contenttypes.models import ContentType
    from wagtail.snippets.models import get_snippet_models
    snippets = get_snippet_models()
    models = [mdl.model_class()
              for mdl in ContentType.objects.filter(app_label=app).all()]
    to_register = [x for x in snippets + models if x is not None]
    registered = set()

    for cls in to_register:
        _register_model(registered, cls, cls in snippets,
                        app, prefix, url_prefix)


def add_apps_with_settings(settings: dict, url_prefix: str) -> None:
    if settings_registry:
        add_app('wagtail.contrib.settings')

    for app in settings['APPS']:
        prefixes = settings.get('PREFIX', {})
        if isinstance(prefixes, str):
            prefix = prefixes
        else:
            prefix = prefixes.get(app, '{app}')
        add_app(app, prefix=prefix, url_prefix=url_prefix)


def add_apps() -> None:
    from .settings import SETTINGS, URL_PREFIX
    add_apps_with_settings(SETTINGS, URL_PREFIX)
