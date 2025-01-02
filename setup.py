# Copyright 2024 Comcast Cable Communications Management, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0


"""Package setup script."""
import setuptools

setuptools.setup(
    name='python-globalcache',
    version='24.10.3',
    packages=setuptools.find_packages('python_globalcache'),
    package_data={'python_globalcache.data': ['*']},
    package_dir={'': 'python_globalcache'},
    install_requires=[],
    extras_require={},
    setup_requires=[],
    tests_require=[],
)
