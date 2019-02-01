import logging
from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
from django.db import models, transaction
from django.conf import settings
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from simple_salesforce.exceptions import SalesforceError, \
    SalesforceResourceNotFound
from .client import SalesforceClient
from .chatter import chatter
from .manager import SalesforceManager
from . import helpers

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class SalesforceModel(models.Model):
    salesforce_id = models.CharField(_('salesforce id'), max_length=254,
                                     null=True)
    sync_at = models.DateTimeField(_('last sync date'), auto_now=False,
                                   auto_now_add=False, null=True)
    modify_at = models.DateTimeField(_('last modify date'), auto_now=True,
                                     auto_now_add=False)
    create_at = models.DateTimeField(_('create date'), auto_now=False,
                                     auto_now_add=True)

    SERIALIZABLE_FIELDS = [models.CharField, models.IntegerField,
                           models.BooleanField, models.EmailField,
                           models.FloatField, models.TextField, models.URLField,
                           models.DecimalField,
                           models.DateTimeField, models.DateField]
    salesforce_table_name = None  # mandatory
    salesforce_key_name = SalesforceClient.DEFAULT_SALESFORCE_KEY_NAME  # remote pk field name
    salesforce_django_key_name = SalesforceClient.DEFAULT_KEY_FIELD_NAME_IN_DJANGO  # local pk field name
    salesforce_read_only = ()
    pull_after_create = False
    fields_map = dict()
    objects = SalesforceManager()

    class Meta:
        abstract = True

    @classmethod
    def get_salesforce_client(cls):
        """get salesforce client for model"""
        return SalesforceClient(salesforce_table_name=cls.salesforce_table_name,
                                salesforce_key_name=cls.salesforce_key_name)

    def get_salesforce_pk_value(self):
        if self.salesforce_django_key_name == SalesforceClient.DEFAULT_KEY_FIELD_NAME_IN_DJANGO:
            return self.salesforce_id
        else:
            return getattr(self, self.salesforce_django_key_name)

    @classmethod
    def get_salesforce_field_name(cls, local_field_name):
        return cls.fields_map.get(local_field_name, None)

    @property
    def is_sync(self):
        if not self.sync_at:
            return False
        return self.sync_at >= self.modify_at

    @classmethod
    def get_fields_map(self):
        # todo get fields map from db_column
        for field in self._meta.fields:
            field.get_attname_column()

    def field_serialize(self, obj, field_name, field_type):
        return helpers.get_serialized_data(obj, field_name, field_type)

    def field_deserialize(self, value, field_name, field_type):
        data = helpers.get_deserialized_data(value, field_type)
        setattr(self, field_name, data)

    def serialize(self, fields_map=None, skip_data_error=False,
                  skip_field_error=False):
        """return a dict include field data which will be sent to salesforce"""
        salesforce_fields = {}

        fields_map = fields_map if fields_map else self.fields_map
        for object_field, salesforce_field in fields_map.items():
            if object_field in self.salesforce_read_only:
                continue

            # deal with `account.salesforce_id
            obj = self
            field_name = object_field.split('.')[-1:][0]
            try:
                obj = helpers.get_nested_object(obj, object_field)
            except Exception as ex:
                if skip_data_error:
                    continue
                else:
                    raise ImproperlyConfigured(
                        '[SalesforceModel.serialize] %s field error >> %s' % (
                            object_field, ex))

            # for `fk.attribute`, fk object maybe None
            if obj is None:
                salesforce_fields[salesforce_field] = None
                continue

            if isinstance(getattr(type(obj), field_name),
                          property):  # skip type checking if it's a property
                field_type = property
            else:
                # type checking
                field_type = type(obj._meta.get_field(field_name))
                if field_type not in self.SERIALIZABLE_FIELDS:
                    if skip_field_error:
                        continue
                    else:
                        raise NotImplementedError(
                            '[SalesforceModel.serialize] Please implement serialize() in subclass as `%s` fields are not serializable' % field_name)

            salesforce_fields[salesforce_field] = self.field_serialize(obj,
                                                                       field_name,
                                                                       field_type)
        return salesforce_fields

    def deserialize(self, obj_data, skip_data_error=False,
                    skip_field_error=False):
        """deserialize object with dict received from SF"""
        for remote_field, value in obj_data.items():
            local_field = helpers.get_salesforce_to_object_mapping_key(self,
                                                                       remote_field)
            if not local_field:
                continue

            # deserialize only handle nested field `*.salesforce_id`
            if local_field.count('.') == 1 and local_field.endswith(
                    '.salesforce_id'):
                if not value is None:
                    fk_name = local_field.split('.')[:1][0]
                    fk_model = self._meta.get_field(fk_name).rel.to
                    objects = fk_model.objects.filter(salesforce_id=value).order_by('id')
                    if objects.count() > 1:
                        log.error(
                            '[%s.deserialize] multiple object have same salesforce_id `%s`' % (
                                fk_model.__name__, value))
                        # todo not raise here, do we need report to master?
                    fk_obj = objects.first()
                    if fk_obj is None:
                        fk_obj = fk_model(
                            salesforce_id=value)  # pull the fk object from salesforce
                        try:
                            fk_obj.pull()
                        except SalesforceError:
                            fk_obj = None

                    if fk_obj:
                        setattr(self, '%s_id' % fk_name, fk_obj.id)
                continue
            elif isinstance(getattr(type(self), local_field), property):
                # local field is property, simply skip
                continue
            elif '.' in local_field:
                if skip_data_error:
                    continue
                else:
                    raise NotImplementedError(
                        '[SalesforceModel.deserialize] Please implement deserialize() to handle `%s` fk field' % local_field)

            # type checking
            field_type = type(self._meta.get_field(local_field))
            if field_type not in self.SERIALIZABLE_FIELDS:
                if skip_field_error:
                    continue
                else:
                    raise NotImplementedError(
                        '[SalesforceModel.deserialize] Please implement deserialize() in subclass as `%s` fields are not serializable' % local_field)

            self.field_deserialize(value, local_field, field_type)

    def push(self, update_fields=None):
        if settings.SALESFORCE_OFFLINE:
            return self.serialize()

        # get salesforce client, update_fields for salesforce field name
        if not self.salesforce_table_name:
            raise ImproperlyConfigured(
                'Set salesforce_table_name for salesforce model %s' % self.__class__.__name__)

        salesforce_client = self.get_salesforce_client()

        try:
            fields = self.serialize()
        except Exception as ex:
            log.error('[%s.serialize] id=%s, %s' % (
                self.__class__.__name__, self.id, ex))
            raise ex

        # apply update_fields
        if update_fields and isinstance(update_fields, (list, tuple)):
            update_fields = frozenset(update_fields)
            excluded_fields = set(list(fields.keys())).difference(update_fields)
            for key in excluded_fields:
                fields.pop(key)

        if not self.salesforce_id:
            if self.salesforce_key_name and self.salesforce_key_name != SalesforceClient.DEFAULT_SALESFORCE_KEY_NAME:
                result = salesforce_client.create_with_custom_key(fields,
                                                                  key=self.get_salesforce_pk_value())
            else:
                result = salesforce_client.create(fields)
            if not result:
                log.error('[%s] #%s failed to push to salesforce' % (
                    self.__class__.__name__, self.id))
            elif result.get('id', None):
                # Save Salesforce ID back into local DB
                self.salesforce_id = result.get('id')
                self.sync_at = timezone.now()
                self.save(update_fields=['salesforce_id', 'sync_at'])
                if self.pull_after_create:
                    self.pull()
                log.info('Salesforce data %s[%s]-%s[%s] created' % (
                    self.salesforce_table_name, self.salesforce_id,
                    self.__class__.__name__, self.id))
        else:
            result = salesforce_client.upsert(self.get_salesforce_pk_value(),
                                              fields)

        return result

    def pull(self):
        """pull a local existed obj"""
        if settings.SALESFORCE_OFFLINE:
            return self

        if not hasattr(self, 'fields_map'):
            raise ImproperlyConfigured(
                'Set fields_map for salesforce model %s' % self.__class__.__name__)

        if not self.salesforce_django_key_name == SalesforceClient.DEFAULT_KEY_FIELD_NAME_IN_DJANGO:
            salesforce_obj = self.get_salesforce_client().get_by_custom_id(
                self.salesforce_django_key_name,
                self.get_salesforce_pk_value())
        else:
            salesforce_obj = self.get_salesforce_client().get(
                self.get_salesforce_pk_value())

        if not salesforce_obj:
            # todo how to deal with remote deleting
            return None

        try:
            self.deserialize(salesforce_obj)
        except Exception as ex:
            log.error('[%s#%s.deserialize] %s, data=%s' % (
                self.__class__.__name__, self.id, ex, salesforce_obj))
            raise ex

        self.save()
        # make sync_at later than modify_at, so is_sync return True
        self.sync_at = timezone.now()
        self.save(update_fields=['sync_at'])
        return self

    def save_and_push(self, *args, **kwargs):
        # update_fields for local field name
        with transaction.atomic():
            self.save(*args, **kwargs)

            update_fields = kwargs.get('update_fields', None)
            if update_fields:
                update_fields = [self.fields_map[field_name] for field_name in
                                 update_fields if
                                 field_name in self.fields_map]

            result = self.push(update_fields=update_fields)
        return result

    def delete_and_push(self, *args, **kwargs):
        salesforce_key = self.get_salesforce_pk_value()
        with transaction.atomic():
            self.delete(*args, **kwargs)
            self.get_salesforce_client().delete(salesforce_key)

    @classmethod
    def get_pull_all_sql(cls):
        """ sql to get whole table """
        remote_update_fields = [x for x in cls.fields_map.values()]
        # always need salesforce id to identify exist or not
        if SalesforceClient.DEFAULT_SALESFORCE_KEY_NAME not in remote_update_fields:
            remote_update_fields.append(
                SalesforceClient.DEFAULT_SALESFORCE_KEY_NAME)
        remote_update_fields.append(
            'IsDeleted')  # fake delete field, builtin on all salesforce table
        sql = 'SELECT %s FROM %s' % (
            ','.join(remote_update_fields), cls.salesforce_table_name)
        return sql

    @classmethod
    def pull_all(cls, sql=None, update_fields=None, create_new=True):
        """ update_fields:local filed name need to be updated
            create_new: whether create new if not existed in local
        """
        if not isinstance(cls, type):
            raise ImproperlyConfigured(
                'pull_all() can only be called from class not object.')

        if settings.SALESFORCE_OFFLINE:
            return [x for x in cls.objects.all()], [], []

        existed_items = []
        new_items = []
        deleted_items = []

        should_delete = True if not sql else False
        sql = sql if sql else cls.get_pull_all_sql()  # customized sql
        salesforce_client = cls.get_salesforce_client()
        data = salesforce_client.query_all(sql)
        if data['totalSize'] and data['done']:
            for obj_data in data['records']:
                if obj_data['IsDeleted']:
                    # skip fake deleted item from salesforce
                    continue

                salesforce_id = obj_data[
                    SalesforceClient.DEFAULT_SALESFORCE_KEY_NAME]
                objects = cls.objects.filter(salesforce_id=salesforce_id)
                if objects.count() > 1:
                    log.error(
                        '[%s.pull_all] multiple object have same salesforce_id `%s`' % (
                            cls.__name__, salesforce_id))
                    # todo not raise here, do we need report to master?
                instance = objects.first()
                is_new = not bool(instance)
                if not instance:
                    instance = cls(salesforce_id=salesforce_id)

                # check all fields if creating new else only check update fields
                try:
                    instance.deserialize(obj_data)
                except Exception as ex:
                    log.error('[%s#%s.deserialize] %s, data=%s' % (
                        cls.__name__, instance.id, ex, obj_data))
                    raise ex

                if not is_new:
                    if update_fields:
                        instance.save(update_fields=update_fields)
                    else:
                        instance.save()

                        # make sync_at later than modify_at, so is_sync return True
                        instance.sync_at = timezone.now()
                        instance.save(update_fields=['sync_at'])
                    existed_items.append(instance)
                else:
                    if create_new:
                        # create_new, update_fields not applied
                        instance.save()
                        instance.sync_at = timezone.now()
                        instance.save(update_fields=['sync_at'])
                    # new instances may need further FK field assignment before save, let subclass handle it
                    new_items.append(instance)

            # clean stale data if pull whole table
            if should_delete:
                existing_ids = [x.id for x in existed_items]
                existing_ids += [x.id for x in new_items]
                delete_items = cls.objects.exclude(id__in=existing_ids)
                deleted_items = [x for x in delete_items]
                delete_items.delete()

        return existed_items, new_items, deleted_items

    @classmethod
    def delete_and_push_multiple(cls, queryset):
        """Bulk deletion of objects"""
        # Create a list of Salesforce IDs
        delete_list = list()
        for obj in queryset:
            delete_list.append({cls.salesforce_key_name: obj.salesforce_id})

        with transaction.atomic():
            # Delete local objects Salesforce objects
            queryset.delete()
            cls.get_salesforce_client().bulk_delete(delete_list)

        return delete_list

    def attach_new_file(self, title, file_path):
        with open(file_path, 'rb') as file_obj:
            file_salesforce_id, download_url = self.attach_new_file_obj(title,
                                                                        file_obj)
        return file_salesforce_id, download_url

    def attach_new_file_obj(self, title, file_obj):
        file_salesforce_id, download_url = self.update_file_obj(title, file_obj,
                                                                None)
        self.link_to_files(file_salesforce_id)
        return file_salesforce_id, download_url

    def update_file(self, title, file_path, file_salesforce_id):
        with open(file_path, 'rb') as file_obj:
            file_salesforce_id, download_url = self.update_file_obj(title,
                                                                    file_obj,
                                                                    file_salesforce_id)
        return file_salesforce_id, download_url

    def update_file_obj(self, title, file_obj, file_salesforce_id):
        # update existed attach file by file object
        success, file_salesforce_id, download_url_or_err = chatter.upload_file_obj(
            title, file_obj, file_salesforce_id)
        if success:
            self.link_to_files(file_salesforce_id)
            return file_salesforce_id, download_url_or_err
        else:
            raise SuspiciousOperation(
                '[simple_django_salesforce.attach_new_file] error: %s' % download_url_or_err)

    def link_to_files(self, file_salesforce_id):
        """link self to a uploaded file, it can be seen in `RELATED` on salesforce"""
        if settings.SALESFORCE_OFFLINE:
            return True, None

        # check exist
        is_existed = False
        try:
            sql = "SELECT Id FROM ContentDocumentLink WHERE ContentDocumentId='%s' and LinkedEntityId='%s' and IsDeleted=false"
            sql = sql % (file_salesforce_id, self.salesforce_id)
            link_record = settings.SALESFORCE_CLIENT.query(sql)
            if link_record['totalSize']:
                return True, link_record['records'][0]['Id']
        except SalesforceResourceNotFound:
            pass

        if not is_existed:
            data = {'LinkedEntityId': self.salesforce_id,
                    'ContentDocumentId': file_salesforce_id, 'ShareType': 'V'}
            try:
                result = settings.SALESFORCE_CLIENT.ContentDocumentLink.create(
                    data)
                return True, result.get('id')
            except SalesforceError as ex:
                return False, ex

    def find_attach_file_by_title(self, title):
        """return contentdocument.id according to title from salesforce"""
        if settings.SALESFORCE_OFFLINE:
            return None

        sql = "SELECT Id, ContentDocumentId FROM ContentDocumentLink WHERE ContentDocument.title = '%s' AND LinkedEntityId = '%s' AND IsDeleted=false"
        sql = sql % (title, self.salesforce_id)
        records = settings.SALESFORCE_CLIENT.query(sql)
        if records['totalSize']:
            return records['records'][0]['ContentDocumentId']
        return None

    def get_first_file_link_by_salesforce_id(self):
        """input entity's salesforce id, return the first attached file's download url"""
        if settings.SALESFORCE_OFFLINE:
            return None

        sql = "SELECT Id, ContentDocumentId FROM ContentDocumentLink WHERE LinkedEntityId = '%s' AND IsDeleted=false"
        sql = sql % self.salesforce_id
        records = settings.SALESFORCE_CLIENT.query(sql)

        if records['totalSize']:
            first_document_id = records['records'][0]['ContentDocumentId']
            success, sf_id, download_url = chatter.get_download_url_by_document_id(
                first_document_id)
            return download_url
        return None
