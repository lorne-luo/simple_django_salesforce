import logging
from django.db import transaction
from django.db import models

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class SalesforceQuerySet(models.query.QuerySet):
    def delete_and_push(self, hard_delete=False):
        with transaction.atomic():
            sf_data = [{'Id': obj.salesforce_id} for obj in self]

            deleted, _rows_count = super(SalesforceQuerySet, self).delete()
            client = self.model.get_salesforce_client()
            if hard_delete:
                result = client.bulk_hard_delete(sf_data)
            else:
                result = client.bulk_delete(sf_data)

                # todo check result

        return deleted, _rows_count

    delete_and_push.alters_data = True
    delete_and_push.queryset_only = True

    def update_and_push(self, **kwargs):
        with transaction.atomic():
            rows = super(SalesforceQuerySet, self).update(**kwargs)
            sf_data = []
            for obj in self:
                data = {'Id': obj.salesforce_id}
                data.update(kwargs)
                sf_data.append(data)

            client = self.model.get_salesforce_client()
            result = client.bulk_update(sf_data)
            # todo check result
        return rows

    update_and_push.alters_data = True

    def sf_exists(self):
        # TODO
        raise NotImplementedError
        # client = self.model.get_salesforce_client()


class SalesforceManager(models.Manager):
    # _queryset_class = SalesforceQuerySet

    def get_queryset(self):
        # this is to use your custom queryset methods
        return SalesforceQuerySet(self.model, using=self._db)
