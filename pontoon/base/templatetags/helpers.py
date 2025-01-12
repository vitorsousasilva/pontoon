from __future__ import absolute_import

import cgi
import datetime
import json

import jinja2
from allauth.socialaccount import providers
from allauth.utils import get_request_param
from django_jinja import library
from fluent.syntax import FluentParser, FluentSerializer, ast
from fluent.syntax.serializer import serialize_expression
from six import text_type
from six.moves.urllib import parse as six_parse

from django import template
from django.conf import settings
from django.contrib.humanize.templatetags import humanize
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.urlresolvers import reverse
from django.db.models import QuerySet
from django.utils.encoding import smart_str
from django.utils.encoding import force_text
from django.utils.functional import Promise
from django.utils.http import is_safe_url


register = template.Library()
parser = FluentParser()
serializer = FluentSerializer()


class DatetimeAwareJSONEncoder(json.JSONEncoder):
    """Default encoder isn't able to handle datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime.date):
            return obj.isoformat()

        return json.JSONEncoder.default(self, obj)


class LazyObjectsJSONEncoder(DatetimeAwareJSONEncoder):
    """Default encoder isn't able to handle Django lazy-objects."""
    def default(self, obj):
        if isinstance(obj, Promise):
            return force_text(obj)

        if isinstance(obj, QuerySet):
            return list(map(str, obj))

        return super(LazyObjectsJSONEncoder, self).default(obj)


@library.global_function
def thisyear():
    """The current year."""
    return jinja2.Markup(datetime.date.today().year)


@library.global_function
def url(viewname, *args, **kwargs):
    """Helper for Django's ``reverse`` in templates."""
    return reverse(viewname, args=args, kwargs=kwargs)


@library.global_function
def return_url(request):
    """Get an url of the previous page."""
    url = request.POST.get('return_url', request.META.get('HTTP_REFERER', '/'))
    if not is_safe_url(url, allowed_hosts=settings.ALLOWED_HOSTS):
        return settings.SITE_URL
    return url


@library.filter
def urlparams(url_, hash=None, **query):
    """Add a fragment and/or query paramaters to a URL.

    New query params will be appended to exising parameters, except duplicate
    names, which will be replaced.
    """
    url = six_parse.urlparse(url_)
    fragment = hash if hash is not None else url.fragment

    # Use dict(parse_qsl) so we don't get lists of values.
    q = url.query
    query_dict = dict(six_parse.parse_qsl(smart_str(q))) if q else {}
    query_dict.update((k, v) for k, v in query.items())

    query_string = _urlencode([(k, v) for k, v in query_dict.items()
                               if v is not None])
    new = six_parse.ParseResult(
        url.scheme, url.netloc, url.path, url.params, query_string, fragment
    )
    return new.geturl()


def _urlencode(items):
    """A Unicode-safe URLencoder."""
    try:
        return six_parse.urlencode(items)
    except UnicodeEncodeError:
        return six_parse.urlencode([(k, smart_str(v)) for k, v in items])


@library.filter
def urlencode(txt):
    """Url encode a path."""
    if isinstance(txt, text_type):
        txt = txt.encode('utf-8')
    return six_parse.quote_plus(txt)


@library.global_function
def static(path):
    return staticfiles_storage.url(path)


@library.filter
def to_json(value):
    return json.dumps(value, cls=LazyObjectsJSONEncoder)


@library.filter
def naturalday(source, arg=None):
    return humanize.naturalday(source, arg)


@library.filter
def naturaltime(source):
    return humanize.naturaltime(source)


@library.filter
def intcomma(source):
    return humanize.intcomma(source)


@library.filter
def metric_prefix(source):
    """
    Format numbers with metric prefixes.

    Inspired by: https://stackoverflow.com/a/9462382
    """
    prefixes = [
        {'value': 1E18, 'symbol': 'E'},
        {'value': 1E15, 'symbol': 'P'},
        {'value': 1E12, 'symbol': 'T'},
        {'value': 1E9, 'symbol': 'G'},
        {'value': 1E6, 'symbol': 'M'},
        {'value': 1E3, 'symbol': 'k'},
        {'value': 1, 'symbol': ''},
    ]

    for prefix in prefixes:
        if source >= prefix['value']:
            break

    # Divide source number by the first lower prefix value
    output = source / prefix['value']

    # Round quotient to 1 decimal point
    output = '{0:.1f}'.format(output)

    # Remove decimal point if 0
    output = output.rstrip('0').rstrip('.')

    # Append prefix symbol
    output += prefix['symbol']

    return output


@library.filter
def comma_or_prefix(source):
    if source >= 100000:
        return metric_prefix(source)
    return humanize.intcomma(source)


@library.filter
def display_permissions(self):
    output = 'Can make suggestions'

    if self.translated_locales:
        if self.is_superuser:
            locales = 'all locales'
        else:
            locales = ', '.join(self.translated_locales)
        output = 'Can submit and approve translations for ' + locales

    return output


@library.filter
def date_status(value, complete):
    """Get date status relative to today."""
    if isinstance(value, datetime.date):
        if not complete:
            today = datetime.date.today()
            if value <= today:
                return 'overdue'
            elif (value - today).days < 8:
                return 'approaching'
    else:
        return 'not'

    return 'normal'


@library.filter
def format_datetime(value, format='full', default='---'):
    if value is not None:
        if format == 'full':
            format = '%A, %B %d, %Y at %H:%M %Z'
        elif format == 'date':
            format = '%B %d, %Y'
        elif format == 'short_date':
            format = '%b %d, %Y'
        elif format == 'time':
            format = '%H:%M %Z'
        return value.strftime(format)
    else:
        return default


@library.filter
def format_timedelta(value):
    if value is not None:
        parts = []
        if value.days > 0:
            parts.append('{0} days'.format(value.days))
        minutes = value.seconds // 60
        seconds = value.seconds % 60
        if minutes > 0:
            parts.append('{0} minutes'.format(minutes))
        if seconds > 0:
            parts.append('{0} seconds'.format(seconds))

        if parts:
            return ', '.join(parts)
        else:
            return '0 seconds'
    else:
        return '---'


@register.filter
@library.filter
def nospam(self):
    return jinja2.Markup(
        cgi.escape(self, True).replace('@', '&#64;').replace('.', '&#46;').replace('\'', '&quot;')
    )


@library.global_function
def provider_login_url(request, provider_id='fxa', **query):
    """
    This function adapts the django-allauth templatetags that don't support jinja2.
    @TODO: land support for the jinja2 tags in the django-allauth.
    """
    provider = providers.registry.by_id(provider_id)

    auth_params = query.get('auth_params', None)
    query['scope'] = 'profile:uid profile:email profile:display_name'
    process = query.get('process', None)

    if auth_params == '':
        del query['auth_params']

    if 'next' not in query:
        next_ = get_request_param(request, 'next')
        if next_:
            query['next'] = next_
        elif process == 'redirect':
            query['next'] = request.get_full_path()
    else:
        if not query['next']:
            del query['next']
    return provider.get_login_url(request, **query)


@library.global_function
def providers_media_js(request):
    """A port of django tag into jinja2"""
    return jinja2.Markup('\n'.join([
        p.media_js(request) for p in providers.registry.get_list()
    ]))


@library.filter
def pretty_url(url):
    """Remove protocol and www"""
    url = url.split('://')[1]
    if url.startswith('www.'):
        url = url[4:]

    return url


@library.filter
def local_url(url, code=None):
    """Replace occurences of `{locale_code} in URL with provided code."""
    code = code or 'en-US'
    return url.format(locale_code=code)


@library.filter
def dict_html_attrs(dict_obj):
    """Render json object properties into a series of data-* attributes."""
    return jinja2.Markup(' '.join(
        [u'data-{}="{}"'.format(k, v) for k, v in dict_obj.items()]
    ))


def _get_default_variant(variants):
    """Return default variant from the list of variants."""
    for variant in variants:
        if variant.default:
            return variant


def _serialize_value(value):
    """Serialize AST values into a simple string."""
    response = ''

    for element in value.elements:
        if isinstance(element, ast.TextElement):
            response += element.value

        elif isinstance(element, ast.Placeable):
            if isinstance(element.expression, ast.SelectExpression):
                default_variant = _get_default_variant(element.expression.variants)
                response += _serialize_value(default_variant.value)
            else:
                response += '{ ' + serialize_expression(element.expression) + ' }'

    return response


@library.filter
def as_simple_translation(source):
    """Transfrom complex FTL-based strings into single-value strings."""
    translation_ast = parser.parse_entry(source)

    # Non-FTL string or string with an error
    if isinstance(translation_ast, ast.Junk):
        return source

    # Value: use entire AST
    if translation_ast.value:
        tree = translation_ast

    # Attributes (must be present in valid AST if value isn't):
    # use AST of the first attribute
    else:
        tree = translation_ast.attributes[0]

    return _serialize_value(tree.value)
