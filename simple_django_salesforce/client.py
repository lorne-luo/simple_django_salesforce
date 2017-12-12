from __future__ import unicode_literals
import functools
import logging
import six

from requests import ConnectionError
from unittest.mock import MagicMock
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import (SalesforceResourceNotFound,
                                          SalesforceError,
                                          SalesforceExpiredSession,
                                          SalesforceMalformedRequest)

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class reconnect_decorator(object):
    RETRY_COUNT_MAX = 3

    def __init__(self, func):
        self.retry_count = 1
        self.func = func

    def __get__(self, obj, objtype):
        """Support instance methods."""
        return functools.partial(self.__call__, obj)

    def wrapper(self, base_client, *args, **kwargs):
        try:
            return_func = self.func(base_client, *args, **kwargs)
            self.retry_count = 1
            return return_func
        except (SalesforceExpiredSession, ConnectionError,
                SalesforceMalformedRequest) as ex:
            # reconnect only catch SalesforceMalformedRequest with `InvalidSessionId` err code
            # SalesforceMalformedRequest('https://ap5.salesforce.com/services/async/38.0/job', 400, '', {'exceptionCode': 'InvalidSessionId', 'exceptionMessage': 'Invalid session id'})
            if isinstance(ex, SalesforceMalformedRequest):
                if isinstance(ex.content, dict) and ex.content.get(
                        'exceptionCode', None) == 'InvalidSessionId':
                    pass
                else:
                    raise ex

            if self.retry_count == self.RETRY_COUNT_MAX:
                raise Exception(
                    'Salesforce connection ended after too many reconnection retries.')

            self.retry_count += 1
            settings.SALESFORCE_CLIENT = Salesforce(
                username=settings.SALESFORCE_API_USER,
                password=settings.SALESFORCE_API_PASSWORD,
                security_token=settings.SALESFORCE_API_TOKEN
            )

        return self.wrapper(base_client, *args, **kwargs)

    def __call__(self, base_client, *args, **kwargs):
        self.retry_count = 1
        return self.wrapper(base_client, *args, **kwargs)


def offline_decorator(*args, **kwargs):
    default_offline_return = None

    # the only argument is mock return, return None with no arguments
    def real_decorator(function):
        @six.wraps(function)
        def wrapper(*args, **kwargs):
            return function(*args, **kwargs)

        return wrapper

    if len(args) == 1 and callable(args[0]):
        if not settings.SALESFORCE_OFFLINE:
            return real_decorator(args[0])
        else:
            return MagicMock(return_value=default_offline_return)
    else:
        if not settings.SALESFORCE_OFFLINE:
            return real_decorator
        else:

            return_value = args[0] if len(args) else default_offline_return
            return MagicMock(return_value=MagicMock(return_value=return_value))


class offline_decorator2(object):
    def __init__(self, arg1):
        """
        If there are decorator arguments, the function
        to be decorated is not passed to the constructor!
        """
        print("Inside __init__()")
        self.arg1 = arg1

    def __call__(self, f):
        """
        If there are decorator arguments, __call__() is only called
        once, as part of the decoration process! You can only give
        it a single argument, which is the function object.
        """
        print("Inside __call__()")

        def wrapped_f(*args):
            print("Inside wrapped_f()")
            print("Decorator arguments:", self.arg1, self.arg2, self.arg3)
            f(*args)
            print("After f(*args)")

        return wrapped_f


class SalesforceClient(object):
    DEFAULT_SALESFORCE_KEY_NAME = 'Id'  # salesforce use `Id` as default id
    DEFAULT_KEY_FIELD_NAME_IN_DJANGO = 'salesforce_id'

    # salesforce_client = None
    # model_client = None
    # bulk_model_client = None
    table_name = None
    key_field_name = None

    def __init__(self, *args, **kwargs):
        self.table_name = kwargs.pop('salesforce_table_name', None)
        self.key_field_name = kwargs.pop('salesforce_key_name',
                                         self.DEFAULT_SALESFORCE_KEY_NAME)

        if not self.table_name:
            raise ImproperlyConfigured(
                'Salesforce client not configured properly, need table_name.')

    @property
    def salesforce_client(self):
        return settings.SALESFORCE_CLIENT

    @property
    def model_client(self):
        return getattr(self.salesforce_client, self.table_name)

    @property
    def bulk_model_client(self):
        return getattr(getattr(self.salesforce_client, 'bulk'), self.table_name)

    def get_pk(self, id):
        if self.key_field_name and self.key_field_name != self.DEFAULT_SALESFORCE_KEY_NAME:
            return "%s/%s" % (self.key_field_name, id)
        else:
            return id

    @classmethod
    def get_salesforce_ids(cls, queryset):
        result = []
        for obj in queryset:
            if not hasattr(obj, obj.salesforce_django_key_name):
                raise ImproperlyConfigured(
                    '[SalesforceClient] %s do not have %s field.' % (
                        cls.__name__, obj.salesforce_django_key_name))
            result.append(
                {obj.salesforce_key_name: obj.get_salesforce_pk_value()})

        return result

    @offline_decorator({'salesforce_id': None})
    @reconnect_decorator
    def get_by_custom_id(self, field_name, id):
        try:
            object = self.model_client.get_by_custom_id(field_name, id)
            return object
        except SalesforceResourceNotFound as ex:
            log.error('[SF.%s.get_by_custom_id] %s' % (self.table_name, ex))
            raise ex

    @offline_decorator({'salesforce_id': None})
    @reconnect_decorator
    def get(self, id):
        try:
            object = self.model_client.get(id)
            return object
        except SalesforceResourceNotFound as ex:
            log.error('[SF.%s.get] %s' % (self.table_name, ex))
            raise ex

    @offline_decorator({'salesforce_id': None})
    @reconnect_decorator
    def create(self, fields):
        if not fields:
            return None

        # create() not accept 'Id' field in post data
        if self.DEFAULT_SALESFORCE_KEY_NAME in fields:
            fields.pop(self.DEFAULT_SALESFORCE_KEY_NAME)
        try:
            obj = self.model_client.create(fields)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.create] %s' % (self.table_name, ex))
            log.error('[SF.%s.create] data=%s' % (self.table_name, fields))
            raise ex

    @offline_decorator({'salesforce_id': None})
    @reconnect_decorator
    def create_with_custom_key(self, fields, key=None):
        if not fields:
            return None

        # create() not accept 'Id' field in post data
        if self.DEFAULT_SALESFORCE_KEY_NAME in fields:
            fields.pop(self.DEFAULT_SALESFORCE_KEY_NAME)
        if key:
            fields[self.key_field_name] = key
        try:
            obj = self.model_client.create(fields)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.create] %s' % (self.table_name, ex))
            log.error('[SF.%s.create] data=%s' % (self.table_name, fields))
            raise ex

    @offline_decorator({'salesforce_id': None})
    @reconnect_decorator
    def update(self, id, fields):
        if not fields or not id:
            return None

        # update() not accept 'Id' field in post data
        if self.DEFAULT_SALESFORCE_KEY_NAME in fields:
            fields.pop(self.DEFAULT_SALESFORCE_KEY_NAME)

        try:
            id = self.get_pk(id)
            obj = self.model_client.update(id, fields)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.update #%s] %s' % (self.table_name, ex, id))
            log.error(
                '[SF.%s.update] id=%s, data=%s' % (self.table_name, id, fields))
            raise ex

    @offline_decorator({'salesforce_id': None})
    @reconnect_decorator
    def upsert(self, id, fields):
        if not fields or not id:
            return None
        # upsert() not accept 'Id' field in post data
        if self.DEFAULT_SALESFORCE_KEY_NAME in fields:
            fields.pop(self.DEFAULT_SALESFORCE_KEY_NAME)

        try:
            id = self.get_pk(id)
            obj = self.model_client.upsert(id, fields, raw_response=True)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.upsert #%s] %s' % (self.table_name, ex, id))
            log.error(
                '[SF.%s.upsert] id=%s, data=%s' % (self.table_name, id, fields))
            raise ex

    @offline_decorator(True)
    @reconnect_decorator
    def delete(self, id):
        if not id:
            return None
        try:
            id = self.get_pk(id)
            obj = self.model_client.delete(id)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.delete #%s] %s' % (self.table_name, ex, id))
            log.error('[SF.%s.delete] id=%s' % (self.table_name, id))
            raise ex

    @offline_decorator
    @reconnect_decorator
    def bulk_create(self, data):
        if not data:
            return None

        try:
            obj = self.bulk_model_client.insert(data)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.bulk_create] %s' % (self.table_name, ex))
            log.error('[SF.%s.bulk_create] data=%s' % (self.table_name, data))
            raise ex

    @offline_decorator
    @reconnect_decorator
    def bulk_update(self, data):
        if not data:
            return None

        try:
            obj = self.bulk_model_client.update(data)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.bulk_update] %s' % (self.table_name, ex))
            log.error('[SF.%s.bulk_update] data=%s' % (self.table_name, data))
            raise ex

    @offline_decorator
    @reconnect_decorator
    def bulk_upsert(self, data, key_field_name=DEFAULT_SALESFORCE_KEY_NAME):
        if not data:
            return None

        try:
            obj = self.bulk_model_client.upsert(data, key_field_name)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.bulk_upsert] %s' % (self.table_name, ex))
            log.error('[SF.%s.bulk_upsert] data=%s' % (self.table_name, data))
            raise ex

    @offline_decorator
    @reconnect_decorator
    def bulk_delete(self, ids):
        if not ids:
            return None

        try:
            obj = self.bulk_model_client.delete(ids)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.bulk_delete] %s' % (self.table_name, ex))
            log.error('[SF.%s.bulk_delete] data=%s' % (self.table_name, ids))
            raise ex

    @offline_decorator
    @reconnect_decorator
    def bulk_hard_delete(self, ids):
        if not ids:
            return None

        try:
            obj = self.bulk_model_client.hard_delete(ids)
            return obj
        except SalesforceError as ex:
            log.error('[SF.%s.bulk_hard_delete] %s' % (self.table_name, ex))
            log.error(
                '[SF.%s.bulk_hard_delete] data=%s' % (self.table_name, ids))
            raise ex

    @offline_decorator
    @reconnect_decorator
    def bulk_delete_queryset(self, queryset):
        if queryset:
            delete_ids = self.get_salesforce_ids(queryset)
            self.bulk_delete(delete_ids)

    @offline_decorator
    @reconnect_decorator
    def bulk_hard_deletequeryset(self, queryset):
        if queryset:
            delete_ids = self.get_salesforce_ids(queryset)
            self.bulk_hard_delete(delete_ids)

    # simply wrap other general method of simple-salesforce
    @offline_decorator
    @reconnect_decorator
    def query(self, sql):
        log.debug('[SF.query] %s' % sql)
        return self.salesforce_client.query(sql)

    @offline_decorator
    @reconnect_decorator
    def query_more(self, sql):
        log.debug('[SF.query_more] %s' % sql)
        return self.salesforce_client.query_more(sql)

    @offline_decorator
    @reconnect_decorator
    def query_all(self, sql):
        log.debug('[SF.query_all] %s' % sql)
        return self.salesforce_client.query_all(sql)

    @reconnect_decorator
    def describe(self):
        return self.model_client.describe()

    @reconnect_decorator
    def metadata(self):
        return self.model_client.metadata()
