#include <boost/config.hpp>
#include <boost/assert/source_location.hpp>

#include <exception>
#include <stdexcept>

namespace boost {

void throw_exception(std::exception const& e) {
    throw e;
}

void throw_exception(std::exception const& e, boost::source_location const&) {
    throw e;
}

}  // namespace boost
