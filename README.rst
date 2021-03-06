************************
Simple Django Salesforce
************************

Simple Django Salesforce is a flexible Salesforce.com sync package for Django project. It provide push & pull interface and partial chatter api to the REST Resource.

You can find out more regarding the format of the results in the `Official Salesforce.com REST API Documentation`_

Differ from `django-salesforce`_, this package is very flexible and easy to override and customize.

Thanks to `simple_salesforce`_, this package heavily based on it.

Currently only tested on Python 3.4 + Django 1.10.


.. _Official Salesforce.com REST API Documentation: http://www.salesforce.com/us/developer/docs/api_rest/index.htm
.. _simple_salesforce: https://github.com/simple-salesforce/simple-salesforce
.. _simple_django_salesforce: https://github.com/lorne-luo/simple_django_salesforce
.. _django-salesforce: https://github.com/django-salesforce/django-salesforce

Setup
-----

Add Salesforce credential in django settings.

.. code-block:: python

    SALESFORCE_API_USER = ''        # Salesforce user username
    SALESFORCE_API_PASSWORD = ''    # Salesforce user password
    SALESFORCE_API_TOKEN = ''       # Salesforce user security token

    # for chatter api to attach files to SF objects
    # refer https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/quickstart.htm
    CHATTER_OAUTH_CLIENT_ID = ''
    CHATTER_OAUTH_CLIENT_SECRET = ''

    CHATTER_API_URL = os.environ.get('CHATTER_API_URL', 'https://test.salesforce.com')
    SALESFORCE_SANDBOX = os.environ.get('SALESFORCE_SANDBOX', True)


Django class define
-------------------

.. code-block:: python

    from simple_django_salesforce.model import SalesforceModel

    class Product(SalesforceModel):
        name = models.CharField(_('name'), max_length=254, blank=True, null=True)

        salesforce_table_name = 'Product__c'  # table name on Salesforce

        # fields map between local and salesforce
        fields_map = {
            'salesforce_id': 'Id',         # salesforce id field defined in SalesforceModel
            'name': 'Name__c',
            'upper_name': 'UpperName__c',  # property source, push only
        }

        @property
        def upper_name(self):
            return self.name.upper()

Basic usage
-----------

.. code-block:: python

    Product.pull_all(create_new=True)  # pull all objects from Salesforce, will create new and delete stale

    product = Product.objects.first()
    product.pull()  # pull single object

    product.name = 'new name'
    product.save_and_push()  # save locally and push to Salesforce, local will be rollback if push failed

    product.push(update_fields=['name'])  # push specified fields to Salesforce without save locally

    product.delete_and_push()  # delete and push to Salesforce


Chatter API Uploading
---------------------

.. code-block:: python

    from simple_django_salesforce.chatter import chatter

    product = Product.objects.first()

    # upload and attach a file to SF object
    file_salesforce_id, download_url = product.attach_new_file('attachment title','/data/test.jpg')

    chatter.download_url(download_url, '/data/test2.jpg')  # download url is protected by token


Auto modeling according to SF
-----------------------------
``simple_django_salesforce`` do not include any model for standard Salesforce object, while it provided the auto modeling command.

Compare to hardcoded standard model, auto modeling command's benefit is when you changed definition on Salesforce still can use it to help to update local model.

To use auto modeling command, you need first install ``simple_django_salesforce`` in django ``INSTALLED_APPS``.

.. code-block:: python

    >> python manage.py sf_model Asset  # Salesforce table name

    G = 'g'
    ML = 'mL'

    UNIT_AND_SIZE_CHOICES = (
        (G, G),
        (ML, ML),
    )

    class Asset(SalesforceModel):
        name = models.CharField(_('name'), max_length=254, blank=True, null=True)
        ......
        unit_and_size__c = models.CharField(_('Unit and Size'), choices=UNIT_AND_SIZE_CHOICES, max_length=255, blank=True, null=True)

        fields_map = {
            'salesforce_id': 'Id',
            'name': 'Name',
            ......
            'unit_and_size__c': 'unit_and_size__c',
        }

Serialization
-------------
It's easy to overwrite default serialization.

.. code-block:: python

    class Subclass(SalesforceModel):
        # method 1: override get_serialized_data() and get_deserialized_data() in subclass
        def get_serialized_data(self, obj, field_name, field_type):
            return get_serialized_data(obj, field_name, field_type

        def get_deserialized_data(self, data, field_type):
           return get_deserialized_data(data, field_type)

        # method 2: you can also update serialization result in serialize() / deserialize(), but not recommend
        def serialize(self):
            # always get return when set the two skip param as true
            data = super(Subclass, self).serialize(skip_data_error=True, skip_field_error=True)
            data['unserializable_field'] = `SERIALIZE_CODES`
            return data

        def deserialize(self, obj_data):
            super(Subclass, self).deserialize(skip_data_error=True, skip_field_error=True)
            setattr(self, `FIELD_NAME`, `DESERIALIZE_CODES`)


