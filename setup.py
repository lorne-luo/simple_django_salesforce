from distutils.core import setup

setup(
    name='simple_django_salesforce',
    version='0.1.0',
    packages=['simple_django_salesforce', 'simple_django_salesforce.management.commands'],
    url='https://github.com/lorne-luo/simple_django_salesforce',
    download_url='https://github.com/lorne-luo/simple_django_salesforce/tarball/latest',
    license='Apache 2.0',
    author='Lorne Luo',
    author_email='dev@luotao.net',
    maintainer='Lorne Luo',
    maintainer_email='dev@luotao.net',
    description='A flexible to push & pull django object to salesforce.',
    keywords='python django push pull sync salesforce salesforce.com',
    install_requires=[
        'Django>=1.11.0',
        'requests[security]',
        'simple_salesforce>=0.73.0',
        'python-magic>=0.4.13',
    ],
    tests_require=[
        'nose>=1.3.0',
        'pytz>=2014.1.1',
        'responses>=0.5.1',
    ],
    test_suite='nose.collector',
)
